from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nfc_os.actions import media_control, run_command, run_script


@dataclass(frozen=True)
class CartridgeMeta:
    """Global NFC OS config loaded alongside tag map."""

    home_uids: frozenset[str]
    double_scan_eject: bool
    presence_mode: bool


@dataclass(frozen=True)
class CartridgeSpec:
    uid: str
    kind: str
    payload: str

    @staticmethod
    def normalize_uid(uid: str) -> str:
        return uid.strip().upper()


def _item_to_spec(item: dict[str, Any]) -> CartridgeSpec:
    uid = CartridgeSpec.normalize_uid(str(item["uid"]))
    if "kind" in item:
        kind = str(item["kind"]).strip().lower()
        payload = str(item.get("payload", ""))
    else:
        action = str(item.get("action", "")).strip().lower()
        payload = str(item.get("payload", ""))
        legacy = {
            "run_command": "command",
            "run_script": "script",
            "media_control": "media_control",
        }
        if action not in legacy:
            raise ValueError(f"Unknown legacy action {action!r} for uid {uid}")
        kind = legacy[action]
    return CartridgeSpec(uid=uid, kind=kind, payload=payload)


def load_cartridge_config(
    path: Path,
) -> tuple[dict[str, CartridgeSpec], CartridgeMeta]:
    with path.open("r", encoding="utf-8") as fp:
        raw = json.load(fp)

    meta_raw = raw.get("meta") or {}
    home_raw = meta_raw.get("home_uids") or meta_raw.get("home_uid")
    if home_raw is None:
        home_uids: set[str] = set()
    elif isinstance(home_raw, str):
        home_uids = {CartridgeSpec.normalize_uid(home_raw)}
    else:
        home_uids = {CartridgeSpec.normalize_uid(str(u)) for u in home_raw}

    double_scan_eject = bool(meta_raw.get("double_scan_eject", True))
    presence_mode = bool(meta_raw.get("presence_mode", True))

    specs: dict[str, CartridgeSpec] = {}
    for item in raw.get("tags", []):
        spec = _item_to_spec(item)
        specs[spec.uid] = spec

    meta = CartridgeMeta(
        home_uids=frozenset(home_uids),
        double_scan_eject=double_scan_eject,
        presence_mode=presence_mode,
    )
    return specs, meta


class CartridgeLauncher:
    """Starts cartridges as embedded targets (url), subprocess (command/script), or stubs."""

    KIND_URL = "url"
    KIND_COMMAND = "command"
    KIND_SCRIPT = "script"
    KIND_MEDIA = "media_control"

    @staticmethod
    def validate(spec: CartridgeSpec) -> None:
        allowed = {
            CartridgeLauncher.KIND_URL,
            CartridgeLauncher.KIND_COMMAND,
            CartridgeLauncher.KIND_SCRIPT,
            CartridgeLauncher.KIND_MEDIA,
        }
        if spec.kind not in allowed:
            raise ValueError(f"Unsupported cartridge kind {spec.kind!r}")

    @staticmethod
    def start_subprocess(spec: CartridgeSpec) -> subprocess.Popen[str]:
        CartridgeLauncher.validate(spec)
        if spec.kind == CartridgeLauncher.KIND_COMMAND:
            return subprocess.Popen(
                spec.payload,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        if spec.kind == CartridgeLauncher.KIND_SCRIPT:
            script_path = Path(spec.payload).expanduser().resolve()
            return subprocess.Popen(
                [str(script_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        raise ValueError(f"start_subprocess not valid for kind {spec.kind}")

    @staticmethod
    def run_inline_synchronous(spec: CartridgeSpec) -> str:
        """Used for media_control / one-shot diagnostics (blocking)."""
        CartridgeLauncher.validate(spec)
        if spec.kind == CartridgeLauncher.KIND_MEDIA:
            return media_control(spec.payload)
        if spec.kind == CartridgeLauncher.KIND_COMMAND:
            return run_command(spec.payload)
        if spec.kind == CartridgeLauncher.KIND_SCRIPT:
            return run_script(spec.payload)
        raise ValueError(f"run_inline_synchronous not valid for kind {spec.kind}")
