from __future__ import annotations

import logging
import os


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("nfc_os")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s level=%(levelname)s msg=%(message)s"
        " uid=%(uid)s action=%(action)s payload=%(payload)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    log_file = os.getenv("NFC_OS_LOG_FILE")
    handlers: list[logging.Handler] = [console]
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

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
