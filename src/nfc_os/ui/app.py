from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
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

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _WEBENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None  # type: ignore[misc, assignment]
    _WEBENGINE_AVAILABLE = False


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
        self.setWindowTitle("NFC OS")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)

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

        self._web_engine: QWebEngineView | None = None
        self._url_fallback: QLabel | None = None
        if not (_WEBENGINE_AVAILABLE and QWebEngineView is not None):
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

        dev_row = QWidget()
        dev_row.setObjectName("DevRow")
        dev_lay = QHBoxLayout(dev_row)
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
        outer.addWidget(dev_row)

        self.setCentralWidget(central_outer)

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
        )

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
            self._status_label.setText(
                f"<b>Running</b><br/>UID {op.uid}<br/>kind <code>{op.kind}</code>"
            )
        elif isinstance(op, UiOpLoadUrl):
            if _WEBENGINE_AVAILABLE and QWebEngineView is not None:
                if self._web_engine is None:
                    self._web_engine = QWebEngineView()
                    self._web_slot_layout.addWidget(self._web_engine, stretch=1)
                self._web_engine.load(op.url)
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


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "config" / "tags.json"
        if candidate.exists():
            return parent
    return Path.cwd()


def run_qt() -> None:
    if os.environ.get("NFC_OS_CONFIG"):
        config_path = Path(os.environ["NFC_OS_CONFIG"]).expanduser().resolve()
    else:
        config_path = _repo_root() / "config" / "tags.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config: {config_path}")

    specs, meta = load_cartridge_config(config_path)
    logger = configure_logging()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    event_queue: queue.Queue = queue.Queue()
    ui_queue: queue.Queue[UiOperation | None] = queue.Queue()

    # Keep GUI alive when launched from startx/autostart where stdin may be closed.
    StdinEventSource(event_queue, shutdown_on_eof=False)
    supervisor_thread, stop_supervisor = start_supervisor_thread(
        event_queue, specs, meta, ui_queue, logger
    )

    window = MainWindow(ui_queue, stop_supervisor, event_queue)
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

    if not _WEBENGINE_AVAILABLE:
        QMessageBox.information(
            window,
            "Qt WebEngine",
            "Qt WebEngine is not installed. URL cartridges will show the raw URL "
            "until PySide6 WebEngine is available on this system.",
        )

    try:
        app.exec()
    finally:
        stop_supervisor.set()
        try:
            event_queue.put(None)
        except Exception:
            pass
        supervisor_thread.join(timeout=3.0)
