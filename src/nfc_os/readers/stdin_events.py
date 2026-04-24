from __future__ import annotations

import queue
import sys
import threading

from nfc_os.nfc_events import NfcMessage


class StdinEventSource:
    """
    Development event source over stdin (works over SSH).

    Lines are forwarded to ``event_queue`` for the supervisor worker.

    Commands (one line each):
      +<UID>   tag inserted (presence in)
      -        tag removed (presence out)
      quit     enqueue shutdown (None)
    """

    def __init__(self, event_queue: queue.Queue) -> None:
        self._event_queue = event_queue
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        try:
            for line in sys.stdin:
                text = line.strip()
                if not text:
                    continue
                lower = text.lower()
                if lower in {"quit", "exit", "q"}:
                    break
                if text.startswith("+"):
                    uid = text[1:].strip()
                    if uid:
                        self._event_queue.put(NfcMessage(kind="tag_in", uid=uid))
                    continue
                if text == "-":
                    self._event_queue.put(NfcMessage(kind="tag_out", uid=None))
                    continue
        finally:
            self._event_queue.put(None)
