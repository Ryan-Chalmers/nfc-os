from __future__ import annotations

import logging
from pathlib import Path

from nfc_os.cartridge import CartridgeLauncher, CartridgeSpec, load_cartridge_config
from nfc_os.readers.base import Reader


class Controller:
    """CLI coordinator: read a tag, resolve config, dispatch cartridge (blocking)."""

    def __init__(self, reader: Reader, config_path: Path, logger: logging.Logger) -> None:
        self.reader = reader
        self.config_path = config_path
        self.logger = logger
        self._specs, _ = load_cartridge_config(config_path)

    def process_once(self) -> None:
        uid = self.reader.get_tag()
        if uid is None:
            return

        spec = self._specs.get(uid)
        if spec is None:
            self.logger.warning("unknown_tag", extra={"uid": uid})
            print(f"Unknown tag: {uid}")
            return

        try:
            if spec.kind == CartridgeLauncher.KIND_URL:
                self.logger.info(
                    "cartridge_url",
                    extra={"uid": uid, "action": "url", "payload": spec.payload},
                )
                print(f"[{uid}] url -> {spec.payload}")
                return
            output = CartridgeLauncher.run_inline_synchronous(spec)
            self.logger.info(
                "cartridge_ok",
                extra={"uid": uid, "action": spec.kind, "payload": spec.payload},
            )
            print(f"[{uid}] {spec.kind} -> {output}")
        except Exception as exc:  # noqa: BLE001
            self.logger.exception(
                "cartridge_fail",
                extra={"uid": uid, "action": spec.kind, "payload": spec.payload},
            )
            print(f"Cartridge failed for {uid}: {exc}")
