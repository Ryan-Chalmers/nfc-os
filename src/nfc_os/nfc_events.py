from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NfcMessage:
    """High-level NFC / lifecycle events for the supervisor loop."""

    kind: Literal["tag_in", "tag_out", "child_exit"]
    uid: str | None = None
