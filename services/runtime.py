"""
Vectrax Service Runtime
========================
Sovereign startup entrypoint that orchestrates:
  - Database initialization
  - FastAPI application (API layer)
  - Structured JSON logging
  - Graceful shutdown handling

Usage:
    python -m services.runtime
    # or
    make run-service
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    """Configure structured JSON-like logging for the service."""
    log_level = os.getenv("VX_LOG_LEVEL", "INFO").upper()
    fmt = (
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Vectrax service."""
    _setup_logging()
    logger = logging.getLogger("vectrax.runtime")

    host = os.getenv("VX_CORE_HOST", "0.0.0.0")
    port = int(os.getenv("VX_CORE_PORT", "8900"))
    env = os.getenv("VX_ENV", "dev")
    reload_enabled = env in ("dev", "local")

    logger.info("=" * 60)
    logger.info("  VECTRAX SERVICE RUNTIME")
    logger.info("  env=%s  host=%s  port=%d  reload=%s", env, host, port, reload_enabled)
    logger.info("=" * 60)

    # Pre-init database
    try:
        from vectrax import db
        db.init_db()
        logger.info("Database initialized at %s", db.DB_PATH)
    except Exception as exc:
        logger.warning("Database pre-init skipped: %s", exc)

    # Start uvicorn
    import uvicorn
    import socket

    # Check if port is already in use
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            logger.error("Port %d is already in use. Please stop the existing process or use a different port (VX_CORE_PORT).", port)
            sys.exit(1)

    uvicorn.run(
        "services.core.app:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
