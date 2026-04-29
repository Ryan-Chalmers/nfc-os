from __future__ import annotations

import argparse
import os
from pathlib import Path

from nfc_os.controller import Controller
from nfc_os.logging_config import configure_logging
from nfc_os.readers.mock import MockReader


def _ensure_local_config(config_path: Path) -> Path:
    if config_path.exists():
        return config_path
    example_path = config_path.with_name("tags.example.json")
    if example_path.exists():
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        return config_path
    raise SystemExit(f"Missing config: {config_path}")


def main_cli(config_path: Path) -> None:
    logger = configure_logging()
    controller = Controller(reader=MockReader(), config_path=config_path, logger=logger)

    print("NFC OS running (CLI MockReader). Press Ctrl+C to stop.")
    try:
        while True:
            controller.process_once()
    except KeyboardInterrupt:
        print("Shutting down NFC OS")


def main() -> None:
    parser = argparse.ArgumentParser(description="NFC OS")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Terminal mock reader using legacy Controller loop",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to tags.json (default: ./config/tags.json)",
    )
    args = parser.parse_args()

    if args.config is not None:
        config_path = args.config
    else:
        here = Path(__file__).resolve().parent
        config_path = here / "config" / "tags.json"
    config_path = _ensure_local_config(config_path)

    use_cli = args.cli or os.environ.get("NFC_OS_UI", "").lower() == "cli"
    if use_cli:
        main_cli(config_path)
        return

    try:
        from nfc_os.ui.app import run_qt
    except ImportError as exc:
        raise SystemExit(
            "Qt UI requires PySide6. Install with: pip install -r requirements.txt\n"
            f"Original error: {exc}"
        ) from exc

    run_qt()


if __name__ == "__main__":
    main()
