from __future__ import annotations

from abc import ABC, abstractmethod


class Reader(ABC):
    """Hardware abstraction contract for tag readers."""

    @staticmethod
    def normalize_uid(uid: str) -> str:
        """Normalize UIDs so mocks and real hardware behave identically."""
        return uid.strip().upper()

    def presence_supported(self) -> bool:
        """True when the stack can detect tag removal via polling or sessions."""
        return False

    @abstractmethod
    def get_tag(self) -> str | None:
        """Return normalized UID for the next scan, or None when skipped."""
        raise NotImplementedError
