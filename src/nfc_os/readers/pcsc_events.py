from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence

from smartcard.CardConnection import CardConnection
from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.Exceptions import CardConnectionException

from nfc_os.nfc_events import NfcMessage

_GET_UID_APDUS = (
    [0xFF, 0xCA, 0x00, 0x00, 0x00],
    [0xFF, 0xCA, 0x00, 0x00, 0x07],
)


def _reader_filter_needle() -> str | None:
    raw = os.environ.get("NFC_OS_PCSC_READER", "").strip()
    return raw.upper() if raw else None


def _reader_matches(reader_label: str, needle: str | None) -> bool:
    if needle is None:
        return True
    return needle in reader_label.upper()


def _read_uid(card: object, logger: logging.Logger) -> str | None:
    """Return uppercase hex UID using ACS direct transmit, or None on failure."""
    create_conn = getattr(card, "createConnection", None)
    if create_conn is None:
        return None
    conn = create_conn()
    if conn is None:
        logger.warning(
            "pcsc_no_connection",
            extra={"uid": "-", "action": "pcsc_no_connection", "payload": str(card)},
        )
        return None

    connect_modes: Sequence[object | None] = (
        None,
        CardConnection.T1_protocol,
        CardConnection.T0_protocol,
        CardConnection.RAW_protocol,
    )
    for mode in connect_modes:
        try:
            if mode is None:
                conn.connect()
            else:
                conn.connect(mode)
        except CardConnectionException:
            continue
        try:
            for apdu in _GET_UID_APDUS:
                try:
                    data, sw1, sw2 = conn.transmit(apdu)
                except CardConnectionException:
                    break
                if sw1 == 0x90 and sw2 == 0x00 and data:
                    return "".join(f"{b:02X}" for b in data)
        finally:
            try:
                conn.disconnect()
            except CardConnectionException:
                pass
            except Exception:
                pass
    return None


class _NfcOsCardObserver(CardObserver):
    def __init__(
        self,
        deliver: Callable[[NfcMessage], None],
        logger: logging.Logger,
        reader_needle: str | None,
    ) -> None:
        super().__init__()
        self._deliver = deliver
        self._logger = logger
        self._reader_needle = reader_needle

    def update(self, observable: object, handlers: object) -> None:
        try:
            addedcards, removedcards = handlers  # type: ignore[misc]
        except (TypeError, ValueError):
            self._logger.warning(
                "pcsc_observer_bad_handlers",
                extra={"uid": "-", "action": "pcsc_observer_bad_handlers", "payload": "-"},
            )
            return

        for card in addedcards:
            reader_label = str(getattr(card, "reader", ""))
            if not _reader_matches(reader_label, self._reader_needle):
                continue
            uid = _read_uid(card, self._logger)
            if uid:
                self._logger.info(
                    "pcsc_tag_in",
                    extra={"uid": uid, "action": "pcsc_tag_in", "payload": reader_label[:120]},
                )
                self._deliver(NfcMessage(kind="tag_in", uid=uid))
            else:
                self._logger.warning(
                    "pcsc_uid_failed",
                    extra={
                        "uid": "-",
                        "action": "pcsc_uid_failed",
                        "payload": reader_label[:120],
                    },
                )

        for card in removedcards:
            reader_label = str(getattr(card, "reader", ""))
            if not _reader_matches(reader_label, self._reader_needle):
                continue
            self._logger.info(
                "pcsc_tag_out",
                extra={"uid": "-", "action": "pcsc_tag_out", "payload": reader_label[:120]},
            )
            self._deliver(NfcMessage(kind="tag_out", uid=None))


def register_pcsc_card_observer(
    deliver: Callable[[NfcMessage], None],
    logger: logging.Logger,
) -> Callable[[], None]:
    """
    Register a global CardMonitor observer that forwards taps via ``deliver``.

    ``deliver`` is invoked from pyscard worker threads; it must be thread-safe
    (typically ``lambda m: event_queue.put(m)``).

    Call the returned callable on application shutdown to remove the observer.
    """
    needle = _reader_filter_needle()
    observer = _NfcOsCardObserver(deliver, logger, needle)
    monitor = CardMonitor()
    monitor.addObserver(observer)
    logger.info(
        "pcsc_observer_registered",
        extra={
            "uid": "-",
            "action": "pcsc_observer_registered",
            "payload": f"reader_filter={needle or 'ALL'}",
        },
    )

    def cleanup() -> None:
        try:
            monitor.deleteObserver(observer)
            logger.info(
                "pcsc_observer_removed",
                extra={"uid": "-", "action": "pcsc_observer_removed", "payload": "-"},
            )
        except Exception:
            logger.exception(
                "pcsc_observer_remove_failed",
                extra={"uid": "-", "action": "pcsc_observer_remove_failed", "payload": "-"},
            )

    return cleanup
