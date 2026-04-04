"""
Vectrax Core Central Service — App Factory
=============================================
FastAPI application with /v1/ versioned routes.

Run:
    uvicorn services.core.app:create_app --factory --port 8900
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import logging
import time as _time

from services.core.config import get_settings
from services.core.routes import (
    health, auth, connectors, events, actions,
    chat, memory, proposals, status, gravitational,
    comm, gateway, billing, universe,
)

logger = logging.getLogger("vectrax.core.app")

# Global start time for uptime tracking
_service_start_time: float = 0.0


def get_uptime() -> float:
    """Return seconds since service start."""
    return round(_time.time() - _service_start_time, 2) if _service_start_time else 0.0


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Vectrax Core",
        version="1.0.0",
        description="Vectrax Core Central Service API",
        docs_url="/v1/docs",
        redoc_url="/v1/redoc",
        openapi_url="/v1/openapi.json",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount all routes under /v1
    app.include_router(health.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(memory.router, prefix="/v1")
    app.include_router(proposals.router, prefix="/v1")
    app.include_router(status.router, prefix="/v1")
    app.include_router(connectors.router, prefix="/v1")
    app.include_router(events.router, prefix="/v1")
    app.include_router(actions.router, prefix="/v1")
    app.include_router(gravitational.router, prefix="/v1")
    app.include_router(comm.router, prefix="/v1")
    app.include_router(gateway.router, prefix="/v1")
    app.include_router(billing.router, prefix="/v1")
    app.include_router(universe.router, prefix="/v1")

    # --- Lifecycle events ---

    @app.on_event("startup")
    async def on_startup():
        global _service_start_time
        _service_start_time = _time.time()
        logger.info("Vectrax Core starting (env=%s)", settings.env)
        # Init database
        try:
            from vectrax import db
            db.init_db()
            logger.info("Database initialized")
        except Exception as exc:
            logger.warning("Database init skipped: %s", exc)

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("Vectrax Core shutting down (uptime=%.1fs)", get_uptime())

    # --- Mount UI (if available) ---
    try:
        from services.ui.routes import router as ui_router
        app.include_router(ui_router)
        # Serve static files
        from fastapi.staticfiles import StaticFiles
        ui_static = Path(__file__).resolve().parent.parent / "ui" / "static"
        if ui_static.exists():
            app.mount("/static", StaticFiles(directory=str(ui_static)), name="static")
    except ImportError:
        pass  # UI module not yet available

    return app


# Allow `uvicorn services.core.app:app` for convenience
app = create_app()
