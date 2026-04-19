"""
Vectrax Stripe Billing
========================
Integración con Stripe para cobros y upgrade automático de tier.

Flujo:
  1. Usuario pide /upgrade en Telegram
  2. Se crea Checkout Session con su user_id como metadata
  3. Usuario paga en Stripe
  4. Webhook recibe checkout.session.completed
  5. Se activa tier PRO automáticamente

Creado: 2026-03-30
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("vectrax.billing")

_STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY", "")
_STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
_STRIPE_TEAM_PRICE_ID = os.environ.get("STRIPE_TEAM_PRICE_ID", "")
_STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def _get_stripe():
    """Lazy import and configure stripe."""
    import stripe
    stripe.api_key = _STRIPE_SECRET
    return stripe


# ---------------------------------------------------------------------------
# Create Checkout Session
# ---------------------------------------------------------------------------

def create_checkout_session(
    user_id: str,
    success_url: str = "https://vectrax.com/success",
    cancel_url: str = "https://vectrax.com/cancel",
) -> Optional[str]:
    """
    Create a Stripe Checkout Session for PRO upgrade.

    Returns the checkout URL, or None on failure.
    """
    if not _STRIPE_SECRET or not _STRIPE_PRICE_ID:
        logger.error("Stripe not configured (missing STRIPE_SECRET_KEY or STRIPE_PRICE_ID)")
        return None

    try:
        stripe = _get_stripe()
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{
                "price": _STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            metadata={
                "vectrax_user_id": user_id,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        logger.info(
            "Checkout session created for %s: %s",
            user_id[:20], session.id,
        )
        return session.url
    except Exception as exc:
        logger.error("Failed to create checkout session: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Create Team Checkout Session
# ---------------------------------------------------------------------------

def create_team_checkout_session(
    owner_id: str,
    team_id: str,
    success_url: str = "https://vectrax.com/team/success",
    cancel_url: str = "https://vectrax.com/team/cancel",
) -> Optional[str]:
    """
    Crea una Stripe Checkout Session para el plan de equipo.
    Al pagarse, activa tier TEAM para el owner y todos los miembros.

    Retorna la URL del checkout o None en caso de fallo.
    """
    price_id = _STRIPE_TEAM_PRICE_ID or _STRIPE_PRICE_ID  # fallback al precio PRO
    if not _STRIPE_SECRET or not price_id:
        logger.error("Stripe team not configured (missing STRIPE_SECRET_KEY or STRIPE_TEAM_PRICE_ID)")
        return None

    try:
        stripe = _get_stripe()
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={
                "vectrax_user_id": owner_id,
                "vectrax_team_id": team_id,
                "plan_type": "team",
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        logger.info(
            "Team checkout session created | owner=%s | team=%s",
            owner_id[:20], team_id,
        )
        return session.url
    except Exception as exc:
        logger.error("Failed to create team checkout session: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Handle Webhook
# ---------------------------------------------------------------------------

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """
    Process a Stripe webhook event.

    Verifies signature, handles checkout.session.completed to upgrade tier.
    Returns dict with status and details.
    """
    if not _STRIPE_WEBHOOK_SECRET:
        logger.warning("Stripe webhook secret not configured")
        return {"status": "error", "message": "webhook secret not configured"}

    try:
        stripe = _get_stripe()
        event = stripe.Webhook.construct_event(
            payload, sig_header, _STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("Webhook signature verification failed")
        return {"status": "error", "message": "invalid signature"}
    except Exception as exc:
        logger.error("Webhook parse error: %s", exc)
        return {"status": "error", "message": str(exc)}

    # --- Handle events ---

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("vectrax_user_id", "")
        team_id = session.get("metadata", {}).get("vectrax_team_id", "")
        plan_type = session.get("metadata", {}).get("plan_type", "pro")
        customer_id = session.get("customer", "")
        subscription_id = session.get("subscription", "")

        if team_id and plan_type == "team":
            # Activar tier TEAM para todo el equipo
            try:
                from vectrax.team_memory import activate_team_tier
                activate_team_tier(team_id)
                _store_billing_info(user_id, customer_id, subscription_id)
                _notify_team_upgrade(user_id, team_id)
                logger.info("TEAM tier activated | team=%s | owner=%s", team_id, user_id[:20])
            except Exception as exc:
                logger.error("Failed to activate team tier: %s", exc)
            return {"status": "ok", "action": "team_tier_upgraded", "team_id": team_id}

        elif user_id:
            _activate_pro(user_id, customer_id, subscription_id)
            return {
                "status": "ok",
                "action": "tier_upgraded",
                "user_id": user_id,
            }
        else:
            logger.warning("Checkout completed but no vectrax_user_id in metadata")
            return {"status": "ok", "action": "no_user_id"}

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_id = sub.get("customer", "")
        _deactivate_pro(customer_id)
        return {
            "status": "ok",
            "action": "tier_downgraded",
            "customer_id": customer_id,
        }

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer", "")
        logger.warning("Payment failed for customer %s", customer_id)
        return {
            "status": "ok",
            "action": "payment_failed",
            "customer_id": customer_id,
        }

    return {"status": "ok", "action": "ignored", "type": event["type"]}


# ---------------------------------------------------------------------------
# Tier management
# ---------------------------------------------------------------------------

def _activate_pro(user_id: str, customer_id: str, subscription_id: str) -> None:
    """Upgrade user to PRO tier after successful payment."""
    try:
        from core.operator.user_tiers import set_tier, Tier
        set_tier(user_id, Tier.PRO)
        logger.info(
            "UPGRADE → PRO | user=%s | customer=%s | sub=%s",
            user_id[:20], customer_id[:20], subscription_id[:20],
        )

        # Store Stripe IDs for future reference
        _store_billing_info(user_id, customer_id, subscription_id)

        # Notify user via Telegram
        _notify_upgrade(user_id)

    except Exception as exc:
        logger.error("Failed to activate PRO for %s: %s", user_id, exc)


def _deactivate_pro(customer_id: str) -> None:
    """Downgrade user back to FREE when subscription ends."""
    try:
        user_id = _get_user_by_customer(customer_id)
        if user_id:
            from core.operator.user_tiers import set_tier, Tier
            set_tier(user_id, Tier.FREE)
            logger.info("DOWNGRADE → FREE | user=%s", user_id[:20])
    except Exception as exc:
        logger.error("Failed to deactivate PRO for customer %s: %s", customer_id, exc)


# ---------------------------------------------------------------------------
# Billing storage (SQLite)
# ---------------------------------------------------------------------------

def _billing_db():
    import sqlite3
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "vault", "billing.db",
    )
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=3)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing (
            user_id         TEXT PRIMARY KEY,
            stripe_customer TEXT NOT NULL DEFAULT '',
            stripe_sub      TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _store_billing_info(user_id: str, customer_id: str, subscription_id: str) -> None:
    import time
    try:
        conn = _billing_db()
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO billing "
            "(user_id, stripe_customer, stripe_sub, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (user_id, customer_id, subscription_id, now, now),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Failed to store billing info: %s", exc)


def _get_user_by_customer(customer_id: str) -> Optional[str]:
    try:
        conn = _billing_db()
        row = conn.execute(
            "SELECT user_id FROM billing WHERE stripe_customer = ?",
            (customer_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def get_billing_status(user_id: str) -> dict:
    """Get billing status for a user."""
    try:
        conn = _billing_db()
        row = conn.execute(
            "SELECT stripe_customer, stripe_sub, status FROM billing WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row:
            return {
                "has_subscription": True,
                "status": row[2],
                "customer_id": row[0],
            }
    except Exception:
        pass
    return {"has_subscription": False, "status": "free"}


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

def _notify_team_upgrade(owner_id: str, team_id: str) -> None:
    """Notifica al dueño y miembros del equipo sobre el upgrade."""
    try:
        import httpx
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return

        # Notificar al owner
        if owner_id.startswith("tg:"):
            chat_id = int(owner_id.split(":", 1)[1])
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": (
                        "🟢 Plan de equipo activado.\n\n"
                        "Todo el equipo tiene acceso ilimitado:\n"
                        "• Memoria compartida activa\n"
                        "• Internet, voz, mapas, mercado\n"
                        "• Sin límite de mensajes\n\n"
                        "El conocimiento del equipo ya está disponible para todos los miembros."
                    ),
                },
                timeout=10,
            )

        # Notificar a los miembros
        try:
            from vectrax.team_memory import list_members
            members = list_members(team_id)
            for m in members:
                uid = m["user_id"]
                if uid != owner_id and uid.startswith("tg:"):
                    cid = int(uid.split(":", 1)[1])
                    httpx.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": cid,
                            "text": "🟢 Tu equipo activó el plan completo. Ya tienes acceso ilimitado.",
                        },
                        timeout=10,
                    )
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Team upgrade notification failed: %s", exc)


def _notify_upgrade(user_id: str) -> None:
    """Send a Telegram message confirming the upgrade."""
    try:
        # Extract chat_id from user_id (format: tg:CHAT_ID)
        if not user_id.startswith("tg:"):
            return
        chat_id = int(user_id.split(":", 1)[1])

        import httpx
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return

        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "Vectrax PRO activado.\n\n"
                    "Memoria completa, voz, mapas, mercado, sin límite de mensajes.\n"
                    "El núcleo opera ahora sin restricciones."
                ),
            },
            timeout=10,
        )
    except Exception:
        pass
