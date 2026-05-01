"""PC/SC monitor child: run as ``python -m nfc_os.readers.pcsc_worker_main``.

Emits one JSON object per line on stdout so the GUI process never loads
``smartcard`` / ``_scard`` alongside Qt WebEngine (avoids Pi segfaults).

Lines:
  ``{"k":"in","u":"HEXUID"}`` — tag present (UID read via ACS pseudo-APDU)
  ``{"k":"out"}`` — tag removed for a filtered reader
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Sequence

from smartcard.CardConnection import CardConnection
from smartcard.CardRequest import CardRequest
from smartcard.Exceptions import CardConnectionException, CardRequestTimeoutException

_GET_UID_APDUS = (
    [0xFF, 0xCA, 0x00, 0x00, 0x00],
    [0xFF, 0xCA, 0x00, 0x00, 0x07],
)


def _reader_needle() -> str | None:
    raw = os.environ.get("NFC_OS_PCSC_READER", "").strip()
    return raw.upper() if raw else None


def _reader_matches(reader_label: str, needle: str | None) -> bool:
    if needle is None:
        return True
    return needle in reader_label.upper()


def _read_uid(card: object, log: logging.Logger) -> str | None:
    create_conn = getattr(card, "createConnection", None)
    if create_conn is None:
        return None
    conn = create_conn()
    if conn is None:
        log.warning("pcsc_no_connection payload=%s", str(card)[:120])
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


def _emit(kind: str, uid: str | None = None) -> None:
    if kind == "in" and uid:
        sys.stdout.write(json.dumps({"k": "in", "u": uid}, separators=(",", ":")) + "\n")
    elif kind == "out":
        sys.stdout.write(json.dumps({"k": "out"}, separators=(",", ":")) + "\n")
    else:
        return
    sys.stdout.flush()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("nfc_os.pcsc_worker")
    needle = _reader_needle()
    log.info("pcsc_worker_start reader_filter=%s", needle or "ALL")

    cards: list[object] = []
    while True:
        cr: CardRequest | None = None
        try:
            cr = CardRequest(timeout=0.25)
            current = cr.waitforcardevent()
        except CardRequestTimeoutException:
            continue
        except Exception as exc:
            log.exception("pcsc_worker_poll_failed: %s", exc)
            time.sleep(0.5)
            continue
        finally:
            if cr is not None:
                try:
                    cr.pcsccardrequest.release()
                except Exception:
                    pass

        added = [c for c in current if c not in cards]
        removed = [c for c in cards if c not in current]
        if not added and not removed:
            continue

        cards = list(current)

        for card in added:
            reader_label = str(getattr(card, "reader", ""))
            if not _reader_matches(reader_label, needle):
                continue
            uid = _read_uid(card, log)
            if uid:
                _emit("in", uid)
            else:
                log.warning("pcsc_uid_failed reader=%s", reader_label[:80])

        for card in removed:
            reader_label = str(getattr(card, "reader", ""))
            if not _reader_matches(reader_label, needle):
                continue
            _emit("out")


if __name__ == "__main__":
    raise SystemExit(main())
