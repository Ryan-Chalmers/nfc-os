from __future__ import annotations

import faulthandler
import logging
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nfc_os.cartridge import load_cartridge_config
from nfc_os.logging_config import configure_logging
from nfc_os.readers.stdin_events import StdinEventSource, process_dev_line
from nfc_os.supervisor import (
    UiOpClearUrl,
    UiOpIdle,
    UiOpLoadUrl,
    UiOpProcessCleared,
    UiOpProcessStarted,
    UiOpRunning,
    UiOpToast,
    UiOperation,
    start_supervisor_thread,
)

_WEBENGINE_CLS_PENDING = object()
_webengine_view_cls: Any = _WEBENGINE_CLS_PENDING


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _machine_is_arm_like() -> bool:
    m = platform.machine().lower()
    return m.startswith("arm") or m == "aarch64"


def _prefer_external_url() -> bool:
    """Embedded Qt WebEngine often SIGSEGVs on Raspberry Pi; prefer a real browser there."""
    if _env_truthy("NFC_OS_URL_EMBEDDED"):
        return False
    if _env_truthy("NFC_OS_URL_EXTERNAL"):
        return True
    return _machine_is_arm_like()


def _should_load_embedded_webengine() -> bool:
    return not _prefer_external_url()


_BROWSER_CANDIDATES: tuple[str, ...] = (
    "chromium-browser",
    "chromium",
    "firefox-esr",
    "firefox",
    "epiphany-browser",
    "midori",
    "falkon",
)


def _find_pi_browser() -> str | None:
    """Locate a real browser on the Pi; ignore $BROWSER (Cursor SSH sets it to a host helper)."""
    explicit = os.environ.get("NFC_OS_BROWSER", "").strip()
    if explicit:
        if "/" in explicit and os.access(explicit, os.X_OK):
            return explicit
        found = shutil.which(explicit)
        if found:
            return found
    for name in _BROWSER_CANDIDATES:
        for prefix in ("/usr/bin/", "/usr/local/bin/", "/snap/bin/"):
            path = prefix + name
            if os.access(path, os.X_OK):
                return path
        found = shutil.which(name)
        if found:
            return found
    return None


def _sanitized_browser_env() -> dict[str, str]:
    """Strip Cursor / SSH helpers that hijack URL launches back to the workstation."""
    env = os.environ.copy()
    cursor_helper = "/.cursor-server/" in env.get("BROWSER", "")
    if cursor_helper or _env_truthy("NFC_OS_DROP_BROWSER_ENV"):
        env.pop("BROWSER", None)
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"
    if not env.get("XAUTHORITY"):
        guess = os.path.expanduser("~/.Xauthority")
        if os.path.exists(guess):
            env["XAUTHORITY"] = guess
    env.pop("PYTHONPATH", None)
    return env


def _build_browser_args(browser: str, url: str) -> list[str]:
    name = os.path.basename(browser).lower()
    if "chromium" in name or "chrome" in name:
        args = [
            browser,
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-translate",
        ]
        if _env_truthy("NFC_OS_BROWSER_KIOSK"):
            args.append("--kiosk")
        args.append(url)
        return args
    if "firefox" in name:
        return [browser, "--new-window", url]
    return [browser, url]


def configure_qt_webengine_chromium_env() -> None:
    """Set Chromium flags before Qt WebEngine is imported (idempotent)."""
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        (
            "--disable-gpu --disable-gpu-compositing --no-sandbox --disable-dev-shm-usage "
            "--disable-extensions --disable-background-networking "
            "--disable-features=TranslateUI"
        ),
    )
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    if _env_truthy("NFC_OS_WEBENGINE_VERBOSE"):
        # Chromium logs to stderr (very noisy); use only while debugging renderer crashes.
        cur = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{cur} --enable-logging=stderr --v=1"


def webengine_available() -> bool:
    return _webengine_view_cls is not _WEBENGINE_CLS_PENDING and _webengine_view_cls is not None


def ensure_webengine_loaded() -> bool:
    """Load Qt WebEngine only after :func:`QApplication` exists (reduces Pi crashes)."""
    global _webengine_view_cls
    if _webengine_view_cls is not _WEBENGINE_CLS_PENDING:
        return _webengine_view_cls is not None
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView as W

        _webengine_view_cls = W
        return True
    except Exception:
        _webengine_view_cls = None
        return False


class _LogEmitter(QObject):
    """Thread-safe bridge: logging.Handler.emit runs in worker threads."""

    text = Signal(str)


class _UiLogHandler(logging.Handler):
    """Append formatted ``nfc_os`` log lines into the Qt UI (queued to GUI thread)."""

    def __init__(self, emitter: _LogEmitter) -> None:
        super().__init__(level=logging.INFO)
        self._emitter = emitter
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s "
                "uid=%(uid)s action=%(action)s payload=%(payload)s"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._emitter.text.emit(msg)
        except Exception:
            self.handleError(record)


class MainWindow(QMainWindow):
    def __init__(
        self,
        ui_queue: queue.Queue[UiOperation | None],
        stop_supervisor: threading.Event,
        event_queue: queue.Queue,
    ) -> None:
        super().__init__()
        self._ui_queue = ui_queue
        self._stop_supervisor = stop_supervisor
        self._event_queue = event_queue
        self._browser_proc: subprocess.Popen[Any] | None = None
        self.setWindowTitle("NFC OS")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)

        self._cursor_hidden = False
        try:
            self._cursor_idle_ms = max(
                500,
                min(60_000, int(os.environ.get("NFC_OS_CURSOR_HIDE_MS", "4000"))),
            )
        except ValueError:
            self._cursor_idle_ms = 4000
        self._cursor_hide_timer = QTimer(self)
        self._cursor_hide_timer.setSingleShot(True)
        self._cursor_hide_timer.timeout.connect(self._hide_cursor_after_idle)

        self._stack = QStackedWidget()
        self._idle_label = QLabel()
        self._idle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._idle_label.setWordWrap(True)
        self._idle_label.setObjectName("IdleLabel")
        # Avoid a solid “blank” screen before the first supervisor UiOpIdle is drained
        # (same dark background as the window, so an empty label looks like no UI).
        self._idle_label.setText(
            "<h1>NFC OS</h1><p>Idle home</p><p style='font-size:18px'>Starting…</p>"
        )

        self._running_page = QWidget()
        running_layout = QVBoxLayout(self._running_page)
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        running_layout.addWidget(self._status_label)

        # WebEngine is created lazily on first URL load. Spawning Chromium at
        # startup (even while the stacked widget shows the idle page) can leave
        # a fullscreen Pi/X11 session black until first navigation.
        self._web_slot = QWidget()
        self._web_slot_layout = QVBoxLayout(self._web_slot)
        self._web_slot_layout.setContentsMargins(0, 0, 0, 0)
        running_layout.addWidget(self._web_slot, stretch=1)

        self._web_engine: Any = None
        self._url_fallback: QLabel | None = None
        if not webengine_available():
            self._url_fallback = QLabel()
            self._url_fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._url_fallback.setWordWrap(True)
            self._web_slot_layout.addWidget(self._url_fallback, stretch=1)

        self._stack.addWidget(self._wrap_center(self._idle_label))
        self._stack.addWidget(self._running_page)

        central_outer = QWidget()
        outer = QVBoxLayout(central_outer)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._stack, stretch=1)

        self._log_emitter = _LogEmitter(self)
        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("UiLog")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(400)
        self._log_view.setMinimumHeight(72)
        self._log_view.setMaximumHeight(120)
        self._log_view.setPlaceholderText("Log — dev lines, cartridge events, ejects…")
        self._log_emitter.text.connect(
            self._append_ui_log,
            Qt.ConnectionType.QueuedConnection,
        )
        outer.addWidget(self._log_view)

        self._dev_row = QWidget()
        self._dev_row.setObjectName("DevRow")
        dev_lay = QHBoxLayout(self._dev_row)
        dev_lay.setContentsMargins(8, 6, 8, 6)
        dev_lay.setSpacing(8)
        dev_hint = QLabel("Test tags:")
        dev_hint.setObjectName("DevHint")
        self._dev_input = QLineEdit()
        self._dev_input.setObjectName("DevInput")
        self._dev_input.setPlaceholderText("+ABCD1234  |  -  |  quit")
        self._dev_input.returnPressed.connect(self._submit_dev_line)
        dev_send = QPushButton("Send")
        dev_send.setObjectName("DevSend")
        dev_send.clicked.connect(self._submit_dev_line)
        dev_lay.addWidget(dev_hint)
        dev_lay.addWidget(self._dev_input, stretch=1)
        dev_lay.addWidget(dev_send)
        outer.addWidget(self._dev_row)

        self.setCentralWidget(central_outer)

        self._debug_chrome_visible = False
        self._log_view.setVisible(False)
        self._dev_row.setVisible(False)
        self._toggle_debug_shortcut = QShortcut(QKeySequence("Ctrl+1"), self)
        self._toggle_debug_shortcut.activated.connect(self._toggle_debug_chrome)

        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.installEventFilter(self)
        self._cursor_hide_timer.start(self._cursor_idle_ms)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_ui_queue)
        self._timer.start(50)
        # Flush any ops already queued (e.g. UiOpIdle) on the next event-loop tick.
        QTimer.singleShot(0, self._drain_ui_queue)
        # Fusion + partial QSS: `color` on QMainWindow does not reliably paint
        # descendant QLabel text; labels keep the default dark palette → invisible
        # on our dark background.
        self.setStyleSheet(
            "QMainWindow { background-color: #0d1117; color: #e6edf3; }"
            "QMainWindow QLabel { color: #e6edf3; background-color: transparent; }"
            "QLabel#IdleLabel { font-size: 20px; }"
            "QStatusBar { color: #e6edf3; }"
            "QWidget#DevRow { background-color: #161b22; border-top: 1px solid #30363d; }"
            "QLabel#DevHint { color: #8b949e; font-size: 13px; }"
            "QLineEdit#DevInput { background-color: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 6px 8px; "
            "font-family: monospace; font-size: 14px; }"
            "QPushButton#DevSend { background-color: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 6px 14px; }"
            "QPushButton#DevSend:hover { background-color: #30363d; }"
            "QPlainTextEdit#UiLog { background-color: #0d1117; color: #8b949e; "
            "border-top: 1px solid #30363d; border-bottom: none; border-left: none; "
            "border-right: none; font-family: monospace; font-size: 11px; "
            "padding: 4px 8px; selection-background-color: #30363d; }"
        )

    def _append_ui_log(self, text: str) -> None:
        self._log_view.appendPlainText(text.rstrip())

    @staticmethod
    def _apply_webengine_safety_settings(view: Any) -> None:
        from PySide6.QtWebEngineCore import QWebEngineSettings

        s = view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        # Do not disable WebGL / accelerated 2D here: with Chromium already started
        # using --disable-gpu, turning those off often yields a permanently black view
        # while the renderer stays alive (YouTube needs a working software GL path).
        prefetch = getattr(QWebEngineSettings.WebAttribute, "DnsPrefetchEnabled", None)
        if prefetch is not None:
            s.setAttribute(prefetch, False)

    def _wire_webengine_page_logging(self, view: Any) -> None:
        """Log load outcome and renderer death (native SIGSEGV still won't produce a Python traceback)."""
        page = view.page()
        page.loadFinished.connect(self._webengine_load_finished)
        if hasattr(page, "renderProcessTerminated"):
            page.renderProcessTerminated.connect(self._webengine_render_process_terminated)
        # Qt 6 / PySide6: `javaScriptConsoleMessage` on QWebEnginePage is an override hook, not a
        # Signal — getattr + .connect raised AttributeError and aborted setup before addWidget/load.

    def _webengine_load_finished(self, ok: bool) -> None:
        logging.getLogger("nfc_os").info(
            "webengine_load_finished",
            extra={"uid": "-", "action": "webengine_load_finished", "payload": f"ok={ok}"},
        )
        if not ok:
            self.statusBar().showMessage(
                "Web page reported load failure (see log: webengine_load_finished ok=false).",
                12000,
            )

    def _webengine_render_process_terminated(
        self,
        status: Any,
        exit_code: int,
    ) -> None:
        try:
            detail = f"{status.name} exit={exit_code}"
        except Exception:
            detail = f"{status!r} exit={exit_code}"
        logging.getLogger("nfc_os").warning(
            "webengine_render_process_terminated",
            extra={
                "uid": "-",
                "action": "webengine_render_process_terminated",
                "payload": detail,
            },
        )

    def _deferred_load_url(self, url: str) -> None:
        log = logging.getLogger("nfc_os")
        if not webengine_available():
            return
        W = _webengine_view_cls
        try:
            if self._web_engine is None:
                self._web_engine = W()
                self._apply_webengine_safety_settings(self._web_engine)
                self._wire_webengine_page_logging(self._web_engine)
                self._web_slot_layout.addWidget(self._web_engine, stretch=1)
            qurl = QUrl(url.strip())
            if not qurl.isValid():
                qurl = QUrl.fromUserInput(url.strip())
            self._web_engine.load(qurl)
            log.info(
                "webengine_navigate",
                extra={"uid": "-", "action": "webengine_navigate", "payload": url[:200]},
            )
        except Exception as exc:
            log.exception(
                "webengine_load_failed",
                extra={
                    "uid": "-",
                    "action": "webengine_load_failed",
                    "payload": str(exc)[:300],
                },
            )
            if self._web_engine is not None:
                try:
                    self._web_slot_layout.removeWidget(self._web_engine)
                except Exception:
                    pass
                self._web_engine.deleteLater()
                self._web_engine = None

    def _toggle_debug_chrome(self) -> None:
        self._debug_chrome_visible = not self._debug_chrome_visible
        self._log_view.setVisible(self._debug_chrome_visible)
        self._dev_row.setVisible(self._debug_chrome_visible)
        state = "shown" if self._debug_chrome_visible else "hidden"
        self.statusBar().showMessage(f"Debug UI {state} (Ctrl+1)", 3000)

    def _hide_cursor_after_idle(self) -> None:
        if self._cursor_hidden:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
        self._cursor_hidden = True

    def _cursor_activity_bump(self) -> None:
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        self._cursor_hide_timer.stop()
        self._cursor_hide_timer.start(self._cursor_idle_ms)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        et = event.type()
        if et in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TabletMove,
        ):
            self._cursor_activity_bump()
        return False

    def _submit_dev_line(self) -> None:
        text = self._dev_input.text()
        self._dev_input.clear()
        process_dev_line(self._event_queue, text, mode="gui_submit")

    @staticmethod
    def _wrap_center(inner: QWidget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.addStretch(1)
        layout.addWidget(inner)
        layout.addStretch(1)
        return holder

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._cursor_hide_timer.stop()
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.removeEventFilter(self)
        if self._cursor_hidden:
            QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        self._terminate_external_browser()
        self._stop_supervisor.set()
        try:
            self._event_queue.put(None)
        except Exception:
            pass
        super().closeEvent(event)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                op = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if op is None:
                QApplication.quit()
                return
            self._apply_op(op)

    def _apply_op(self, op: UiOperation) -> None:
        if isinstance(op, UiOpIdle):
            self._stack.setCurrentIndex(0)
            self._idle_label.setText(
                "<h1>NFC OS</h1><p>Idle home</p><p style='font-size:18px'>"
                + op.hint.replace("\n", "<br/>")
                + "</p>"
            )
            self._clear_web()
        elif isinstance(op, UiOpRunning):
            self._stack.setCurrentIndex(1)
            is_url = op.kind == "url"
            self._status_label.setVisible(not is_url)
            if not is_url:
                self._status_label.setText(
                    f"<b>Running</b><br/>UID {op.uid}<br/>kind <code>{op.kind}</code>"
                )
        elif isinstance(op, UiOpLoadUrl):
            if _prefer_external_url():
                self._launch_external_browser(op.url.strip())
            elif webengine_available():
                url = op.url
                QTimer.singleShot(50, lambda u=url: self._deferred_load_url(u))
            elif self._url_fallback is not None:
                self._url_fallback.setText(
                    "Qt WebEngine is not available. URL cartridge:<br/>"
                    f"<code>{op.url}</code><br/><br/>"
                    "Install WebEngine components for PySide6 on this platform."
                )
        elif isinstance(op, UiOpClearUrl):
            self._clear_web()
        elif isinstance(op, UiOpToast):
            self.statusBar().showMessage(op.message, 8000)
        elif isinstance(op, UiOpProcessStarted):
            self.statusBar().showMessage(f"Child PID {op.pid}", 8000)
        elif isinstance(op, UiOpProcessCleared):
            self.statusBar().showMessage("Child process cleared", 4000)

    def _clear_web(self) -> None:
        if self._web_engine is not None:
            self._web_engine.setHtml("<html><body></body></html>")
        elif self._url_fallback is not None:
            self._url_fallback.clear()
        self._terminate_external_browser()
        self._status_label.setVisible(True)

    def _terminate_external_browser(self) -> None:
        proc = self._browser_proc
        self._browser_proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except Exception:
            logging.getLogger("nfc_os").exception(
                "external_browser_terminate_failed",
                extra={
                    "uid": "-",
                    "action": "external_browser_terminate_failed",
                    "payload": "-",
                },
            )

    def _launch_external_browser(self, raw_url: str) -> None:
        log = logging.getLogger("nfc_os")
        self._terminate_external_browser()
        self._status_label.setVisible(True)

        browser = _find_pi_browser()
        if browser is None:
            log.warning(
                "no_pi_browser",
                extra={
                    "uid": "-",
                    "action": "no_pi_browser",
                    "payload": raw_url[:200],
                },
            )
            self._status_label.setText(
                "<b>No browser installed on this device.</b><br/>"
                f"<code>{raw_url}</code><br/><br/>"
                "Install one with: <code>sudo apt install chromium</code>"
                " (or <code>chromium-browser</code> on older releases),"
                " or set <code>NFC_OS_BROWSER=/path/to/browser</code>.<br/><br/>"
                "<small>Remove the tag to return home when presence mode is on.</small>"
            )
            return

        env = _sanitized_browser_env()
        args = _build_browser_args(browser, raw_url)
        try:
            proc = subprocess.Popen(
                args,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            log.exception(
                "external_browser_spawn_failed",
                extra={
                    "uid": "-",
                    "action": "external_browser_spawn_failed",
                    "payload": f"{browser}: {exc}",
                },
            )
            self._status_label.setText(
                f"<b>Browser launch failed:</b> {browser}<br/>"
                f"<code>{raw_url}</code>"
            )
            return

        self._browser_proc = proc
        log.info(
            "external_browser_spawned",
            extra={
                "uid": "-",
                "action": "external_browser_spawned",
                "payload": f"pid={proc.pid} browser={os.path.basename(browser)} display={env.get('DISPLAY', '?')} url={raw_url[:160]}",
            },
        )
        self._status_label.setText(
            f"<b>Opening on this display:</b> {os.path.basename(browser)}<br/>"
            f"<code>{raw_url}</code><br/><br/>"
            "<small>Remove the tag to return home when presence mode is on.</small>"
        )


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        tags_path = parent / "config" / "tags.json"
        example_path = parent / "config" / "tags.example.json"
        if tags_path.exists() or example_path.exists():
            return parent
    return Path.cwd()


def _ensure_local_config(config_path: Path) -> Path:
    if config_path.exists():
        return config_path
    example_path = config_path.with_name("tags.example.json")
    if example_path.exists():
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        return config_path
    raise FileNotFoundError(f"Missing config: {config_path}")


def run_qt() -> None:
    configure_qt_webengine_chromium_env()

    if os.environ.get("NFC_OS_CONFIG"):
        config_path = Path(os.environ["NFC_OS_CONFIG"]).expanduser().resolve()
    else:
        config_path = _repo_root() / "config" / "tags.json"
    config_path = _ensure_local_config(config_path)

    specs, meta = load_cartridge_config(config_path)
    logger = configure_logging()
    faulthandler.enable(all_threads=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    if _should_load_embedded_webengine():
        if not ensure_webengine_loaded():
            logger.warning(
                "webengine_unavailable",
                extra={
                    "uid": "-",
                    "action": "webengine_unavailable",
                    "payload": "URL cartridges will use the text fallback",
                },
            )
    else:
        logger.info(
            "webengine_skipped_for_url",
            extra={
                "uid": "-",
                "action": "webengine_skipped_for_url",
                "payload": "ARM default: URL cartridges use the system browser; set NFC_OS_URL_EMBEDDED=1 to embed WebEngine",
            },
        )

    event_queue: queue.Queue = queue.Queue()
    ui_queue: queue.Queue[UiOperation | None] = queue.Queue()

    pcsc_cleanup: Callable[[], None] | None = None
    use_pcsc = os.environ.get("NFC_OS_USE_PCSC", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    # Keep GUI alive when launched from startx/autostart where stdin may be closed.
    StdinEventSource(event_queue, shutdown_on_eof=False)
    supervisor_thread, stop_supervisor = start_supervisor_thread(
        event_queue, specs, meta, ui_queue, logger
    )

    window = MainWindow(ui_queue, stop_supervisor, event_queue)
    ui_log_handler = _UiLogHandler(window._log_emitter)
    logger.addHandler(ui_log_handler)

    window.showFullScreen()
    window.raise_()
    window.activateWindow()

    logger.info(
        "qt_ui_show",
        extra={
            "uid": "-",
            "action": "qt_ui_show",
            "payload": f"fullscreen config={config_path.name}",
        },
    )

    if use_pcsc:
        try:
            if _env_truthy("NFC_OS_PCSC_INPROCESS"):
                from nfc_os.readers.pcsc_events import register_pcsc_card_observer

                pcsc_cleanup = register_pcsc_card_observer(
                    lambda m: event_queue.put(m),
                    logger,
                )
            else:
                from nfc_os.readers.pcsc_subprocess import register_pcsc_subprocess

                pcsc_cleanup = register_pcsc_subprocess(event_queue, logger)
        except ImportError as exc:
            logger.warning(
                "pcsc_import_failed",
                extra={
                    "uid": "-",
                    "action": "pcsc_import_failed",
                    "payload": str(exc)[:300],
                },
            )
        except Exception as exc:
            logger.exception(
                "pcsc_register_failed",
                extra={
                    "uid": "-",
                    "action": "pcsc_register_failed",
                    "payload": str(exc)[:300],
                },
            )

    if _should_load_embedded_webengine() and not webengine_available():
        QMessageBox.information(
            window,
            "Qt WebEngine",
            "Qt WebEngine is not installed. URL cartridges will show the raw URL "
            "until PySide6 WebEngine is available on this system.",
        )

    try:
        app.exec()
    finally:
        if pcsc_cleanup is not None:
            pcsc_cleanup()
        logger.removeHandler(ui_log_handler)
        ui_log_handler.close()
        stop_supervisor.set()
        try:
            event_queue.put(None)
        except Exception:
            pass
        supervisor_thread.join(timeout=3.0)
