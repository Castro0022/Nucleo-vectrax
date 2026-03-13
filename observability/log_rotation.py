"""
Vectrax Observability — Log Rotation
=======================================
Configures rotating file handlers for platform logs.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOGS_DIR = os.path.join(os.path.expanduser("~/Vectrax"), "logs")

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5


def setup_rotating_log(
    name: str = "vectrax",
    filename: str = "vectrax_platform.log",
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Create a logger with a RotatingFileHandler.
    Returns the configured logger.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    filepath = os.path.join(LOGS_DIR, filename)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(
            filepath,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
