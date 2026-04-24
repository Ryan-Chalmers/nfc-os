from __future__ import annotations

from nfc_os.readers.base import Reader


class MockReader(Reader):
    """Terminal-input reader for local and SSH-based development."""

    def get_tag(self) -> str | None:
        raw = input("scan> ")
        if not raw.strip():
            return None
        return self.normalize_uid(raw)
