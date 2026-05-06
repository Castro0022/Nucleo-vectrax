"""
core/sovereignty/policy.py — Reglas declarativas.

Define qué razones de envío existen, cuáles son siempre permitidas,
cuáles requieren opt-in del usuario, y cuáles son admin-only.

Cualquier código que quiera enviar un mensaje al user DEBE declarar
una `SendReason`. Esto convierte la decisión "¿este envío se permite?"
en una pregunta declarativa, no procedural.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ===========================================================================
# Razones de envío
# ===========================================================================

class SendReason(str, Enum):
    """Por qué el sistema quiere enviarle un mensaje al usuario.

    Cada call a un sender (sendMessage, sendAudio, sendPhoto, sendVoice,
    sendVenue) debe declarar una de estas razones. La autoridad central
    decide si la combinación (razón + user + estado de opt-in) está
    permitida.
    """

    # --- Siempre permitidas (no requieren opt-in) ---
    USER_REPLY = "user_reply"            # respuesta directa al último mensaje del user
    USER_ACK = "user_ack"                # confirmación de recibo (location, photo upload)
    USER_COMMAND = "user_command"        # respuesta a /comando explícito
    ERROR_TO_USER = "error_to_user"      # error que el user generó (rate limit, retry)

    # --- Requieren opt-in (consent.grant_consent) ---
    SCHEDULED_REMINDER = "scheduled_reminder"   # /remind, alarmas registradas
    PROACTIVE_INSIGHT = "proactive_insight"     # follow-up del proactive_engine
    PROACTIVE_LEAD = "proactive_lead"           # lead silencioso
    DAILY_DIGEST = "daily_digest"               # resumen diario
    MARKET_ALERT = "market_alert"               # señales de mercado

    # --- Admin-only (creador o roles privilegiados) ---
    ADMIN_BROADCAST = "admin_broadcast"  # mensaje masivo (anuncios)
    SYSTEM_NOTICE = "system_notice"      # mantenimiento, downtime planificado

    # --- Default (sin razón declarada) ---
    UNDECLARED = "undeclared"            # bug: el código olvidó declarar razón


# ===========================================================================
# Tipos de consent (qué puede activar el user)
# ===========================================================================

class ConsentType(str, Enum):
    """Categorías de mensajes que el user puede activar/desactivar."""

    REMINDERS = "reminders"              # SCHEDULED_REMINDER
    PROACTIVE = "proactive"              # PROACTIVE_INSIGHT + PROACTIVE_LEAD
    DAILY_DIGEST = "daily_digest"        # DAILY_DIGEST
    MARKET_ALERTS = "market_alerts"      # MARKET_ALERT


# ===========================================================================
# Mapping: SendReason → ConsentType requerido
# ===========================================================================

_REASON_REQUIRES_CONSENT = {
    SendReason.SCHEDULED_REMINDER: ConsentType.REMINDERS,
    SendReason.PROACTIVE_INSIGHT: ConsentType.PROACTIVE,
    SendReason.PROACTIVE_LEAD: ConsentType.PROACTIVE,
    SendReason.DAILY_DIGEST: ConsentType.DAILY_DIGEST,
    SendReason.MARKET_ALERT: ConsentType.MARKET_ALERTS,
}


# ===========================================================================
# Sets declarativos
# ===========================================================================

ALWAYS_ALLOWED_REASONS = frozenset({
    SendReason.USER_REPLY,
    SendReason.USER_ACK,
    SendReason.USER_COMMAND,
    SendReason.ERROR_TO_USER,
})

OPT_IN_REQUIRED_REASONS = frozenset({
    SendReason.SCHEDULED_REMINDER,
    SendReason.PROACTIVE_INSIGHT,
    SendReason.PROACTIVE_LEAD,
    SendReason.DAILY_DIGEST,
    SendReason.MARKET_ALERT,
})

ADMIN_ONLY_REASONS = frozenset({
    SendReason.ADMIN_BROADCAST,
    SendReason.SYSTEM_NOTICE,
})


def consent_for_reason(reason: SendReason) -> Optional[ConsentType]:
    """Devuelve el ConsentType que el reason requiere, o None si no requiere."""
    return _REASON_REQUIRES_CONSENT.get(reason)


# ===========================================================================
# Decision
# ===========================================================================

class DecisionStatus(str, Enum):
    ALLOWED = "ALLOWED"          # send permitido, hacelo
    BLOCKED = "BLOCKED"          # send rechazado, no envíes
    OBSERVED = "OBSERVED"        # en modo observe: NO bloquea, pero loggea como bloqueado-en-enforce


@dataclass(frozen=True)
class Decision:
    """Resultado de require_authorization()."""

    status: DecisionStatus
    reason: SendReason
    chat_id: int
    user_id: str
    rule: str                            # "always_allowed" | "consent_granted" | "no_consent" | "admin_check_failed" | "undeclared"
    notes: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        """True si el caller debe enviar (incluye OBSERVED en modo observe)."""
        return self.status in (DecisionStatus.ALLOWED, DecisionStatus.OBSERVED)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason.value,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "rule": self.rule,
            "notes": self.notes,
            "metadata": self.metadata,
        }
