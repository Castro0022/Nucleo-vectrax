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

# Load .env for API keys (eToro, OpenAI, Telegram, etc.)
try:
    from dotenv import load_dotenv
    _env_path = Path(_project_root) / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
except ImportError:
    pass

import logging
import time as _time

from services.core.config import get_settings
from services.core.routes import (
    health, auth, connectors, events, actions,
    chat, memory, proposals, status, gravitational,
    comm, gateway, billing, universe, webhook, recovery,
    monitor, ideas, dashboard, market_live, ingest_api,
    pattern_performance, census, domain_knowledge_api,
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
    # Convenience alias: also expose /health at root for monitoring tools
    app.include_router(health.router)
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
    app.include_router(webhook.router, prefix="/v1")
    app.include_router(recovery.router, prefix="/v1")
    app.include_router(monitor.router, prefix="/v1")
    app.include_router(ideas.router,   prefix="/v1")
    app.include_router(dashboard.router, prefix="/v1")
    app.include_router(market_live.router, prefix="/v1")
    app.include_router(ingest_api.router, prefix="/v1")
    app.include_router(pattern_performance.router, prefix="/v1")
    app.include_router(census.router, prefix="/v1")
    app.include_router(domain_knowledge_api.router, prefix="/v1")

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

        # Init TelegramGateway for webhook mode (USE_WEBHOOK=1).
        # Instantiated without calling .run() — we only need handlers + pool.
        import os as _os
        if _os.environ.get("USE_WEBHOOK", "0") == "1":
            try:
                from vectrax.telegram_gateway import TelegramGateway, _load_token
                gw = TelegramGateway(_load_token())
                # Set _running=True so handlers that check it work correctly;
                # but we do NOT call gw.run() — no polling loop, no heartbeat.
                gw._running = True
                webhook.set_gateway_instance(gw)
                try:
                    gw._write_heartbeat()  # seed gateway heartbeat at startup
                except Exception:
                    pass
                logger.info("TelegramGateway instantiated for webhook mode (no polling loop)")
            except Exception as exc:
                logger.exception("Webhook mode init failed: %s", exc)
                # Do NOT raise — app should still serve other routes.
        else:
            logger.info("Webhook mode disabled (USE_WEBHOOK!=1); long-poll gateway runs separately")

        # --- Recovery engine (feature-flagged, default off) ---
        # Iteration 1: detector gateway_silent + strategy classA_revert_webhook.
        # Controlled by RESILIENCE_ENABLED env var. When RESILIENCE_DRY_RUN=1
        # the executor simulates actions instead of running them — safe mode
        # for initial observation.
        if _os.environ.get("RESILIENCE_ENABLED", "0") == "1":
            try:
                from core.recovery.registry import build_engine
                dry = _os.environ.get("RESILIENCE_DRY_RUN", "0") == "1"
                orch, _exec = build_engine(dry_run=dry)
                recovery.set_orchestrator(orch)
                recovery.set_executor(_exec)
                orch.start()
                app.state.recovery_orchestrator = orch
                app.state.recovery_executor = _exec
                logger.info(
                    "Recovery engine started (dry_run=%s, detectors=%d)",
                    dry, len(orch.snapshot()),
                )
            except Exception as exc:
                logger.exception("Recovery engine init failed (non-fatal): %s", exc)
        else:
            logger.info("Recovery engine disabled (RESILIENCE_ENABLED!=1)")

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("Vectrax Core shutting down (uptime=%.1fs)", get_uptime())
        # Stop recovery engine if running (flush ledger, stop detector threads)
        orch = getattr(app.state, "recovery_orchestrator", None)
        if orch is not None:
            try:
                orch.stop()
                logger.info("Recovery engine stopped cleanly")
            except Exception as exc:
                logger.warning("Recovery engine stop failed: %s", exc)

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
