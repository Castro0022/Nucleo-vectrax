"""
core/sovereignty/authorizer.py — Autoridad central de envío.

require_authorization(chat_id, reason, user_id, ...) -> Decision

Esta es la única función que decide si un mensaje sale o no.
Cualquier sender (telegram_gateway, pipeline_worker, proactive_engine, etc.)
debe pasar por aquí.

Modos:
  observe  (default): convierte BLOCKED → OBSERVED (no bloquea, sólo loggea).
  enforce          : bloquea sends no autorizados.
  off              : bypass total (devuelve ALLOWED con rule="bypass").
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from core.sovereignty.consent import has_consent
from core.sovereignty.ledger import record_decision
from core.sovereignty.policy import (
    ADMIN_ONLY_REASONS,
    ALWAYS_ALLOWED_REASONS,
    Decision,
    DecisionStatus,
    OPT_IN_REQUIRED_REASONS,
    SendReason,
    consent_for_reason,
)

logger = logging.getLogger("vectrax.sovereignty.authorizer")


_VALID_MODES = ("observe", "enforce", "off")


def sovereignty_mode() -> str:
    """Lee SOVEREIGNTY_MODE del env. Default: observe."""
    m = (os.environ.get("SOVEREIGNTY_MODE") or "observe").strip().lower()
    if m not in _VALID_MODES:
        return "observe"
    return m


def _creator_id() -> str:
    """ID del creador (admin universal). Por defecto el chat_id de Mario."""
    return (os.environ.get("VX_CREATOR_ID") or "2030762343").strip()


def _user_key(chat_id: int, user_id: Optional[str]) -> str:
    """Normaliza el user_id a 'tg:<chat_id>' si no se pasó explícitamente."""
    if user_id:
        return str(user_id)
    return f"tg:{chat_id}"


def require_authorization(
    chat_id: int,
    reason: SendReason,
    user_id: Optional[str] = None,
    payload_size: int = 0,
    notes: str = "",
) -> Decision:
    """Decide si el envío (chat_id, reason) está autorizado.

    Reglas:
      1) modo == off                    → ALLOWED  (rule="bypass")
      2) reason en ALWAYS_ALLOWED       → ALLOWED  (rule="always_allowed")
      3) reason en ADMIN_ONLY:
           - user es creador           → ALLOWED  (rule="admin_check")
           - user no es creador        → BLOCKED  (rule="admin_check_failed")
      4) reason en OPT_IN_REQUIRED:
           - has_consent(user, ct)     → ALLOWED  (rule="consent_granted")
           - sin consent                → BLOCKED  (rule="no_consent")
      5) reason == UNDECLARED           → BLOCKED  (rule="undeclared")
      6) cualquier otro caso            → BLOCKED  (rule="unknown_reason")

    En modo observe, todo BLOCKED se convierte a OBSERVED (loggea pero
    permite el envío). El caller usa decision.is_allowed para saber
    si debe enviar realmente.

    Cada decisión se persiste en el ledger.
    """
    mode = sovereignty_mode()
    uid = _user_key(chat_id, user_id)

    # 1) bypass total
    if mode == "off":
        decision = Decision(
            status=DecisionStatus.ALLOWED,
            reason=reason,
            chat_id=chat_id,
            user_id=uid,
            rule="bypass",
            notes=notes or "SOVEREIGNTY_MODE=off",
        )
        record_decision(decision, payload_size=payload_size)
        return decision

    status: DecisionStatus
    rule: str
    final_notes = notes

    # 2) siempre permitidas
    if reason in ALWAYS_ALLOWED_REASONS:
        status = DecisionStatus.ALLOWED
        rule = "always_allowed"

    # 3) admin-only
    elif reason in ADMIN_ONLY_REASONS:
        # admite varias formas: 'tg:2030762343', '2030762343', etc.
        creator = _creator_id()
        normalized = uid.split(":")[-1]
        if normalized == creator or uid == creator:
            status = DecisionStatus.ALLOWED
            rule = "admin_check"
        else:
            status = DecisionStatus.BLOCKED
            rule = "admin_check_failed"
            final_notes = final_notes or f"non-creator user_id={uid}"

    # 4) opt-in requerido
    elif reason in OPT_IN_REQUIRED_REASONS:
        ct = consent_for_reason(reason)
        if ct is not None and has_consent(uid, ct):
            status = DecisionStatus.ALLOWED
            rule = "consent_granted"
        else:
            status = DecisionStatus.BLOCKED
            rule = "no_consent"
            final_notes = final_notes or (
                f"missing consent: {ct.value if ct else 'unknown'}"
            )

    # 5) sin razón declarada → bug en el caller
    elif reason == SendReason.UNDECLARED:
        status = DecisionStatus.BLOCKED
        rule = "undeclared"
        final_notes = final_notes or "caller did not declare SendReason"

    # 6) razón fuera de catálogo (no debería ocurrir con Enum)
    else:
        status = DecisionStatus.BLOCKED
        rule = "unknown_reason"
        final_notes = final_notes or f"unknown reason: {reason!r}"

    # Modo observe: convierte BLOCKED → OBSERVED (no bloquea de verdad)
    if status == DecisionStatus.BLOCKED and mode == "observe":
        status = DecisionStatus.OBSERVED

    decision = Decision(
        status=status,
        reason=reason,
        chat_id=chat_id,
        user_id=uid,
        rule=rule,
        notes=final_notes,
    )
    record_decision(decision, payload_size=payload_size)

    # Logging: blocked y observed van a WARNING, allowed a DEBUG
    if status == DecisionStatus.BLOCKED:
        logger.warning(
            "SOVEREIGNTY BLOCKED chat_id=%s reason=%s rule=%s notes=%s",
            chat_id, reason.value, rule, final_notes,
        )
    elif status == DecisionStatus.OBSERVED:
        logger.info(
            "SOVEREIGNTY OBSERVED chat_id=%s reason=%s rule=%s notes=%s "
            "(would block in enforce mode)",
            chat_id, reason.value, rule, final_notes,
        )
    else:
        logger.debug(
            "SOVEREIGNTY ALLOWED chat_id=%s reason=%s rule=%s",
            chat_id, reason.value, rule,
        )

    return decision
