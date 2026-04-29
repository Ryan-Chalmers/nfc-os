from __future__ import annotations

import logging
import os
from pathlib import Path


def _default_log_file() -> Path:
    state_home = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    primary = state_home / "nfc-os" / "nfc-os.log"
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        return primary
    except OSError:
        return Path("/tmp/nfc-os.log")


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("nfc_os")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s level=%(levelname)s msg=%(message)s"
        " uid=%(uid)s action=%(action)s payload=%(payload)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    log_file = os.getenv("NFC_OS_LOG_FILE") or str(_default_log_file())
    handlers: list[logging.Handler] = [console]
    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        # Keep console logging alive even if file path is not writable.
        pass

    class DefaultFieldsFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            for field in ("uid", "action", "payload"):
                if not hasattr(record, field):
                    setattr(record, field, "-")
            return True

    logger.handlers.clear()
    logger.addFilter(DefaultFieldsFilter())
    for handler in handlers:
        logger.addHandler(handler)

    return logger
