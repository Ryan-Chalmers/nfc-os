from __future__ import annotations

import logging
import queue
import subprocess
import threading
from dataclasses import dataclass

from nfc_os.cartridge import CartridgeLauncher, CartridgeMeta, CartridgeSpec
from nfc_os.nfc_events import NfcMessage


@dataclass(frozen=True)
class UiOpIdle:
    hint: str = ""


@dataclass(frozen=True)
class UiOpRunning:
    uid: str
    kind: str
    payload: str


@dataclass(frozen=True)
class UiOpLoadUrl:
    url: str


@dataclass(frozen=True)
class UiOpClearUrl:
    pass


@dataclass(frozen=True)
class UiOpToast:
    message: str


@dataclass(frozen=True)
class UiOpProcessStarted:
    uid: str
    pid: int


@dataclass(frozen=True)
class UiOpProcessCleared:
    pass


UiOperation = (
    UiOpIdle
    | UiOpRunning
    | UiOpLoadUrl
    | UiOpClearUrl
    | UiOpToast
    | UiOpProcessStarted
    | UiOpProcessCleared
)


def terminate_process(proc: subprocess.Popen[str] | None, timeout: float = 3.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


class SupervisorEngine:
    """Presence-first cartridge state machine (runs on a worker thread)."""

    def __init__(
        self,
        specs: dict[str, CartridgeSpec],
        meta: CartridgeMeta,
        logger: logging.Logger,
        ui_queue: queue.Queue[UiOperation | None],
        event_queue: queue.Queue[NfcMessage | None],
    ) -> None:
        self._specs = specs
        self._meta = meta
        self._logger = logger
        self._ui_queue = ui_queue
        self._event_queue = event_queue
        self._current_uid: str | None = None
        self._current_spec: CartridgeSpec | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._watcher_stop = threading.Event()
        self._watcher: threading.Thread | None = None

    def _notify_ui(self, op: UiOperation) -> None:
        self._ui_queue.put(op)

    def _stop_watcher(self) -> None:
        self._watcher_stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=1.0)
        self._watcher_stop.clear()
        self._watcher = None

    def _start_watcher(self, proc: subprocess.Popen[str]) -> None:
        self._stop_watcher()
        self._watcher_stop = threading.Event()
        event_queue = self._event_queue

        def _watch() -> None:
            proc.wait()
            if not self._watcher_stop.is_set():
                event_queue.put(NfcMessage(kind="child_exit", uid=None))

        self._watcher = threading.Thread(target=_watch, daemon=True)
        self._watcher.start()

    def eject(self, reason: str) -> None:
        self._logger.info(
            "eject",
            extra={"reason": reason, "uid": self._current_uid or "-"},
        )
        self._stop_watcher()
        terminate_process(self._proc)
        if self._proc is not None:
            self._notify_ui(UiOpProcessCleared())
        self._proc = None
        self._current_uid = None
        self._current_spec = None
        self._notify_ui(UiOpClearUrl())
        home = ", ".join(sorted(self._meta.home_uids)) or "none"
        self._notify_ui(
            UiOpIdle(
                hint=(
                    "Insert cartridge: +UID  |  Remove: -  |  Double-scan same tag ejects"
                    f"  |  Home UIDs: {home}"
                )
            )
        )

    def _start_spec(self, spec: CartridgeSpec) -> None:
        self._current_uid = spec.uid
        self._current_spec = spec
        self._notify_ui(UiOpRunning(uid=spec.uid, kind=spec.kind, payload=spec.payload))
        if spec.kind == CartridgeLauncher.KIND_URL:
            self._notify_ui(UiOpLoadUrl(url=spec.payload))
        elif spec.kind in (CartridgeLauncher.KIND_COMMAND, CartridgeLauncher.KIND_SCRIPT):
            self._proc = CartridgeLauncher.start_subprocess(spec)
            self._notify_ui(UiOpProcessStarted(uid=spec.uid, pid=self._proc.pid))
            self._start_watcher(self._proc)
        elif spec.kind == CartridgeLauncher.KIND_MEDIA:
            text = CartridgeLauncher.run_inline_synchronous(spec)
            self._notify_ui(UiOpToast(message=text))
        else:
            self._notify_ui(UiOpToast(message=f"Unsupported kind: {spec.kind}"))

    def handle(self, msg: NfcMessage) -> None:
        if msg.kind == "child_exit":
            if self._current_spec and self._current_spec.kind in (
                CartridgeLauncher.KIND_COMMAND,
                CartridgeLauncher.KIND_SCRIPT,
            ):
                self.eject("child_exit")
            return

        if msg.kind == "tag_out":
            if self._meta.presence_mode and self._current_uid is not None:
                self.eject("tag_out")
            return

        if msg.kind != "tag_in" or not msg.uid:
            return

        uid = CartridgeSpec.normalize_uid(msg.uid)

        if uid in self._meta.home_uids:
            if self._current_uid is not None:
                self.eject("home_uid")
            return

        if self._current_uid is not None:
            if uid == self._current_uid and self._meta.double_scan_eject:
                self.eject("double_scan")
                return
            if uid in self._specs:
                self.eject("switch_cartridge")
                self._start_spec(self._specs[uid])
                return
            self._logger.warning("unknown_tag_while_running", extra={"uid": uid})
            self._notify_ui(UiOpToast(message=f"Unknown tag while running: {uid}"))
            return

        if uid not in self._specs:
            self._logger.warning("unknown_tag", extra={"uid": uid})
            self._notify_ui(UiOpToast(message=f"Unknown tag: {uid}"))
            return

        self._start_spec(self._specs[uid])


def supervisor_worker(
    event_queue: queue.Queue[NfcMessage | None],
    specs: dict[str, CartridgeSpec],
    meta: CartridgeMeta,
    ui_queue: queue.Queue[UiOperation | None],
    stop_event: threading.Event,
    logger: logging.Logger,
) -> None:
    engine = SupervisorEngine(specs, meta, logger, ui_queue, event_queue)
    engine._notify_ui(
        UiOpIdle(hint="Insert cartridge: +UID  |  Remove: -  |  Quit: quit")
    )

    while not stop_event.is_set():
        try:
            msg = event_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if msg is None:
            break
        engine.handle(msg)

    engine.eject("shutdown")
    ui_queue.put(None)


def start_supervisor_thread(
    event_queue: queue.Queue[NfcMessage | None],
    specs: dict[str, CartridgeSpec],
    meta: CartridgeMeta,
    ui_queue: queue.Queue[UiOperation | None],
    logger: logging.Logger,
) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()

    def _run() -> None:
        supervisor_worker(event_queue, specs, meta, ui_queue, stop, logger)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, stop
