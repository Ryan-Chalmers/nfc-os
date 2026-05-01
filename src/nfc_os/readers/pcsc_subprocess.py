"""Run PC/SC monitoring in a separate Python process (no ``smartcard`` in the Qt app)."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from nfc_os.nfc_events import NfcMessage

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent


def _pythonpath_env() -> dict[str, str]:
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "").strip()
    root = str(_SRC_ROOT)
    env["PYTHONPATH"] = f"{root}:{prev}" if prev else root
    return env


def register_pcsc_subprocess(
    event_queue: queue.Queue[NfcMessage | None],
    logger: logging.Logger,
) -> Callable[[], None]:
    """
    Spawn ``python -m nfc_os.readers.pcsc_worker_main`` and forward lines to ``event_queue``.

    Keeps ``smartcard``/``_scard`` out of the Qt WebEngine process to avoid hard crashes
    on Raspberry Pi when URL cartridges load Chromium.
    """
    cmd = [sys.executable, "-u", "-m", "nfc_os.readers.pcsc_worker_main"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_pythonpath_env(),
        )
    except Exception:
        logger.exception(
            "pcsc_subprocess_spawn_failed",
            extra={"uid": "-", "action": "pcsc_subprocess_spawn_failed", "payload": "-"},
        )
        raise

    stop = threading.Event()

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "pcsc_subprocess_bad_line",
                        extra={
                            "uid": "-",
                            "action": "pcsc_subprocess_bad_line",
                            "payload": line[:200],
                        },
                    )
                    continue
                k = obj.get("k")
                if k == "in" and obj.get("u"):
                    uid = str(obj["u"]).upper()
                    logger.info(
                        "pcsc_tag_in",
                        extra={
                            "uid": uid,
                            "action": "pcsc_tag_in",
                            "payload": "subprocess",
                        },
                    )
                    event_queue.put(NfcMessage(kind="tag_in", uid=uid))
                elif k == "out":
                    logger.info(
                        "pcsc_tag_out",
                        extra={"uid": "-", "action": "pcsc_tag_out", "payload": "subprocess"},
                    )
                    event_queue.put(NfcMessage(kind="tag_out", uid=None))
        except Exception:
            if not stop.is_set():
                logger.exception(
                    "pcsc_subprocess_stdout_pump_failed",
                    extra={"uid": "-", "action": "pcsc_subprocess_stdout_pump_failed", "payload": "-"},
                )

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        try:
            for line in proc.stderr:
                if stop.is_set():
                    break
                line = line.rstrip()
                if line:
                    logger.info(
                        "pcsc_worker_stderr",
                        extra={"uid": "-", "action": "pcsc_worker_stderr", "payload": line[:500]},
                    )
        except Exception:
            pass

    t_out = threading.Thread(target=_pump_stdout, name="pcsc-stdout", daemon=True)
    t_err = threading.Thread(target=_pump_stderr, name="pcsc-stderr", daemon=True)
    t_out.start()
    t_err.start()

    logger.info(
        "pcsc_subprocess_started",
        extra={
            "uid": "-",
            "action": "pcsc_subprocess_started",
            "payload": f"pid={proc.pid} reader_filter={os.environ.get('NFC_OS_PCSC_READER', '') or 'ALL'}",
        },
    )

    def cleanup() -> None:
        stop.set()
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except Exception:
            logger.exception(
                "pcsc_subprocess_cleanup_failed",
                extra={"uid": "-", "action": "pcsc_subprocess_cleanup_failed", "payload": "-"},
            )
        t_out.join(timeout=1.0)
        t_err.join(timeout=0.5)
        logger.info(
            "pcsc_subprocess_stopped",
            extra={"uid": "-", "action": "pcsc_subprocess_stopped", "payload": "-"},
        )

    return cleanup
