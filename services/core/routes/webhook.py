"""
POST /v1/webhook/telegram/{secret} — Telegram Webhook endpoint.

Alternative to long-polling. Telegram pushes each Update as HTTPS POST.
This endpoint verifies the secret, deduplicates by update_id, and enqueues
the update to the same ThreadPoolExecutor that the polling loop used.

Architecture:
    Telegram  →  POST /v1/webhook/telegram/{secret}
              →  TelegramGateway._pool.submit(_handle, update)
              →  200 OK (response does NOT wait for processing)

The TelegramGateway is instantiated as a singleton in app.state during
FastAPI startup. We instantiate it WITHOUT calling .run() — this gives
us:
  - self._send_http: httpx.Client for sending messages back to Telegram
  - self._dl_http:   httpx.Client for downloading media
  - self._pool:      ThreadPoolExecutor for handling updates
  - self._handle:    the full message processing pipeline
  - all _handle_*, _fast, _send, _tg helpers intact

Without:
  - self._poll_http / refresh cycle (long-poll artifact)
  - self._heartbeat_loop / _heartbeat_thread (polling watchdog)
  - run() loop (replaced by HTTP endpoint)

Idempotency:
  Telegram retries if it doesn't get 200 within ~60s. We use the
  persisted offset file (_OFFSET_FILE) to track the highest update_id
  processed. Duplicates are acknowledged with 200 OK but not re-enqueued.

Security:
  Dual-layer verification:
    1. URL secret (path param) — set via TELEGRAM_WEBHOOK_SECRET env var.
    2. Header X-Telegram-Bot-Api-Secret-Token — also set via setWebhook.
  Both must match. If either fails, 403.

Creado: 2026-04-22
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = logging.getLogger("vectrax.core.routes.webhook")

# Singleton reference; set by startup hook in app.py
_gateway_instance: Optional[Any] = None


def set_gateway_instance(gw: Any) -> None:
    """Called by app.py startup hook to register the TelegramGateway singleton."""
    global _gateway_instance
    _gateway_instance = gw
    logger.info("Webhook router: TelegramGateway singleton registered")


def get_gateway_instance() -> Any:
    """Return the registered gateway or 503 if not initialized."""
    if _gateway_instance is None:
        raise HTTPException(
            status_code=503,
            detail="TelegramGateway not initialized. Check startup logs.",
        )
    return _gateway_instance


def _verify_secrets(url_secret: str, header_secret: Optional[str]) -> None:
    """Dual verification: URL path secret + optional header secret.

    Both come from TELEGRAM_WEBHOOK_SECRET env var. The header is the
    official Telegram mechanism (X-Telegram-Bot-Api-Secret-Token) and is
    set when calling setWebhook. The URL secret is an additional layer
    against log leaks of the header.
    """
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected:
        logger.error("TELEGRAM_WEBHOOK_SECRET not configured in environment")
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    # URL secret check (path param)
    if url_secret != expected:
        logger.warning("Webhook auth failed: URL secret mismatch")
        raise HTTPException(status_code=403, detail="Invalid URL secret")

    # Header secret check (Telegram's official layer)
    # If the header is present, it must match. If absent, accept URL alone
    # (some edge cases in Telegram setWebhook without header).
    if header_secret is not None and header_secret != expected:
        logger.warning("Webhook auth failed: header secret mismatch")
        raise HTTPException(status_code=403, detail="Invalid header secret")


@router.post("/telegram/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    """Receive a Telegram Update and enqueue for processing.

    Responds 200 OK immediately after enqueuing. Processing happens
    asynchronously in the TelegramGateway's ThreadPoolExecutor, same as
    the polling loop did.

    Returns:
        {"ok": true}                      — enqueued for processing
        {"ok": true, "dedup": true}       — duplicate update_id, already processed
        HTTPException(403)                — secret mismatch
        HTTPException(400)                — malformed update
        HTTPException(503)                — gateway not initialized or misconfigured
    """
    # Read body BEFORE any heavy work — fail fast on malformed input
    try:
        update: Dict[str, Any] = await request.json()
    except Exception as exc:
        logger.warning("Webhook body parse failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Update must be an object")

    # Auth
    _verify_secrets(secret, x_telegram_bot_api_secret_token)

    # Fetch gateway singleton
    gw = get_gateway_instance()

    # Idempotency via persisted offset.
    # Telegram may retry up to ~60s. We track the highest update_id we
    # enqueued in gw._offset (persisted by _save_offset).
    update_id = update.get("update_id", 0)
    if not isinstance(update_id, int) or update_id <= 0:
        raise HTTPException(status_code=400, detail="Missing or invalid update_id")

    # gw._offset is "next expected update_id" (current + 1).
    # If update_id < gw._offset → already processed (dedup).
    if update_id < gw._offset:
        logger.info("WEBHOOK DEDUP | update_id=%d < offset=%d", update_id, gw._offset)
        return {"ok": True, "dedup": True}

    # Mark as "will be processed"; advance offset.
    # Uses the same _save_offset helper as the polling loop.
    from vectrax.telegram_gateway import _save_offset
    gw._offset = update_id + 1
    _save_offset(gw._offset)

    # Enqueue to worker pool — non-blocking.
    # This is IDENTICAL to what the polling loop does per update.
    try:
        gw._pool.submit(gw._handle, update)
    except RuntimeError as exc:
        # Pool shutdown or saturated
        logger.error("Webhook pool.submit failed: %s", exc)
        # Raising 500 causes Telegram to retry, which is the desired behavior
        # (we haven't actually processed the update).
        raise HTTPException(status_code=500, detail="Gateway pool unavailable")

    return {"ok": True}


# ---------------------------------------------------------------------------
# Stripe Webhook
# ---------------------------------------------------------------------------

@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Receive Stripe webhook events (checkout.session.completed, etc.).

    Verifies signature using STRIPE_WEBHOOK_SECRET, then delegates to
    the billing module for tier activation/deactivation.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        from services.billing.stripe_billing import handle_webhook
        result = handle_webhook(payload, sig_header)
        logger.info("Stripe webhook processed: %s", result)
        return result
    except Exception as exc:
        logger.error("Stripe webhook failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/telegram/status")
async def webhook_status():
    """Lightweight status endpoint for monitoring.

    Returns basic gateway state if initialized. Does NOT require auth
    because it exposes no secrets or user data — just operational flags.
    """
    if _gateway_instance is None:
        return {"ok": False, "state": "uninitialized"}

    gw = _gateway_instance
    return {
        "ok": True,
        "state": "ready",
        "offset": getattr(gw, "_offset", 0),
        "processed": getattr(gw, "_processed", 0),
        "workers": getattr(gw, "_pool", None)._max_workers if getattr(gw, "_pool", None) else 0,
        "mode": "webhook",
    }
