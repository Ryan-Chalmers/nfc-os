from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Literal

from nfc_os.nfc_events import NfcMessage

_logger = logging.getLogger("nfc_os")


def process_dev_line(
    event_queue: queue.Queue,
    line: str,
    *,
    mode: Literal["stdin_reader", "gui_submit"],
) -> bool:
    """
    Interpret one dev/debug line (same semantics as :class:`StdinEventSource`).

    Commands (one line each):

    - ``+<UID>`` — tag inserted (``tag_in``)
    - ``-`` — tag removed (``tag_out``)
    - ``quit`` / ``exit`` / ``q`` — in ``stdin_reader`` mode, return ``True`` so
      the stdin thread exits without shutting down the app. In ``gui_submit``
      mode, enqueue supervisor shutdown (``None`` on ``event_queue``).
    """
    text = line.strip()
    if not text:
        return False
    if mode == "gui_submit":
        _logger.info(
            "dev_line",
            extra={"uid": "-", "action": "dev_line", "payload": text[:300]},
        )
    lower = text.lower()
    if lower in {"quit", "exit", "q"}:
        if mode == "gui_submit":
            event_queue.put(None)
        return mode == "stdin_reader"
    if text.startswith("+"):
        uid = text[1:].strip()
        if uid:
            event_queue.put(NfcMessage(kind="tag_in", uid=uid))
        return False
    if text == "-":
        event_queue.put(NfcMessage(kind="tag_out", uid=None))
        return False
    return False


class StdinEventSource:
    """
    Development event source over stdin (works over SSH).

    Lines are forwarded to ``event_queue`` for the supervisor worker; see
    :func:`process_dev_line` for command syntax.
    """

    def __init__(self, event_queue: queue.Queue, *, shutdown_on_eof: bool = False) -> None:
        self._event_queue = event_queue
        self._shutdown_on_eof = shutdown_on_eof
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        try:
            for line in sys.stdin:
                if process_dev_line(self._event_queue, line, mode="stdin_reader"):
                    break
        finally:
            # In GUI/kiosk launches stdin is often not interactive and can close
            # immediately; do not auto-shutdown unless explicitly requested.
            if self._shutdown_on_eof:
                self._event_queue.put(None)
