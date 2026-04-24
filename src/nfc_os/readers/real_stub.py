from __future__ import annotations

from nfc_os.readers.base import Reader


class RealReader(Reader):
    """Placeholder for Raspberry Pi NFC hardware integration."""

    def presence_supported(self) -> bool:
        return False

    def get_tag(self) -> str | None:
        raise NotImplementedError(
            "RealReader is not implemented yet. Add NFC library integration next."
        )
