"""
Vectrax Core — Billing Routes
================================
Stripe integration endpoints.

  POST /v1/billing/checkout   — create checkout session (returns URL)
  POST /v1/billing/webhook    — Stripe webhook receiver
  GET  /v1/billing/status     — billing status for current user

Creado: 2026-03-30
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger("vectrax.core.routes.billing")


# ---------------------------------------------------------------------------
# POST /v1/billing/checkout
# ---------------------------------------------------------------------------

@router.post("/checkout")
async def create_checkout(request: Request):
    """
    Create a Stripe Checkout Session for PRO upgrade.

    Accepts JSON body with:
      - user_id: Vectrax user ID (e.g. "tg:123456")

    Returns:
      - url: Stripe Checkout URL to redirect user to
    """
    body = await request.json()
    user_id = body.get("user_id", "")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required",
        )

    from services.billing.stripe_billing import create_checkout_session
    url = create_checkout_session(user_id)

    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session",
        )

    return {"url": url}


# ---------------------------------------------------------------------------
# POST /v1/billing/webhook
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe webhook receiver.

    Stripe sends events here (checkout.session.completed, etc.).
    Signature is verified against STRIPE_WEBHOOK_SECRET.
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    from services.billing.stripe_billing import handle_webhook
    result = handle_webhook(payload, sig)

    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "webhook error"),
        )

    return result


# ---------------------------------------------------------------------------
# GET /v1/billing/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def billing_status(user_id: str):
    """
    Get billing status for a user.

    Query params:
      - user_id: Vectrax user ID

    Returns subscription status and tier info.
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required",
        )

    from services.billing.stripe_billing import get_billing_status
    from core.operator.user_tiers import get_tier

    billing = get_billing_status(user_id)
    tier = get_tier(user_id)

    return {
        "user_id": user_id,
        "tier": tier.value,
        **billing,
    }
