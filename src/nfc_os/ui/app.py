from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nfc_os.cartridge import load_cartridge_config
from nfc_os.logging_config import configure_logging
from nfc_os.readers.stdin_events import StdinEventSource
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

        self._running_page = QWidget()
        running_layout = QVBoxLayout(self._running_page)
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        running_layout.addWidget(self._status_label)

        self._web: QWidget | None = None
        if _WEBENGINE_AVAILABLE and QWebEngineView is not None:
            self._web = QWebEngineView()
            running_layout.addWidget(self._web, stretch=1)
        else:
            self._url_fallback = QLabel()
            self._url_fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._url_fallback.setWordWrap(True)
            running_layout.addWidget(self._url_fallback, stretch=1)

        self._stack.addWidget(self._wrap_center(self._idle_label))
        self._stack.addWidget(self._running_page)
        self.setCentralWidget(self._stack)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_ui_queue)
        self._timer.start(50)
        self.setStyleSheet(
            "QMainWindow { background-color: #0d1117; color: #e6edf3; }"
            "QLabel#IdleLabel { font-size: 20px; }"
            "QStatusBar { color: #e6edf3; }"
        )

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
            if self._web is not None:
                self._web.load(op.url)  # type: ignore[union-attr]
            else:
                self._url_fallback.setText(  # type: ignore[attr-defined]
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
        if self._web is not None:
            self._web.setHtml("<html><body></body></html>")  # type: ignore[union-attr]
        else:
            self._url_fallback.clear()  # type: ignore[attr-defined]


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
