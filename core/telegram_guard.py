"""
Telegram Output Guard — Silent Mode by Default
================================================
Controls what reaches the user's Telegram chat. Blocks all telemetry,
metrics, ideas, digests, and observability messages. The user's chat
should feel like a conversation, not a monitoring console.

Blocked categories:
  idea/*, gravity/*, router/*, telemetry/*, metrics/*,
  digest/*, observability/*, info/*, debug/*

Allowed:
  - Direct replies to user questions
  - Critical alerts (system down, data loss risk)
  - Actions requiring explicit creator approval
  - Errors affecting functionality

If the user wants to see telemetry, they ask explicitly via commands:
  /reporte, /estado, /auditoria, /universo, /metricas, /observaciones

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("vectrax.telegram_guard")

# ---------------------------------------------------------------------------
# Blocked patterns — messages matching ANY of these are suppressed
# ---------------------------------------------------------------------------

_BLOCKED_PREFIXES = (
    "📊 Router Digest",
    "📊 Resumen",
    "📈 Mercado programado",
    "🔔 ",                    # automatic reminders
    "💡 Nueva idea",
    "💡 New idea",
    "📡 Telemetry",
    "📉 Métricas",
    "🔍 Observación",
    "📋 Reporte automático",
    "🧠 Gravity update",
    "🌌 Universe growth",
    "⚙️ Sistema:",
    "[INTENT:",               # intent classification leaks
    "DEBUG",
)

_BLOCKED_PATTERNS = re.compile(
    r"(?:"
    r"Router Digest"
    r"|router_telemetry"
    r"|gravity.*growth"
    r"|universe.*growth"
    r"|nueva idea\s+(?:HIGH|MEDIUM|LOW)"
    r"|new idea\s+(?:HIGH|MEDIUM|LOW)"
    r"|IDEA-[A-F0-9]{8}"
    r"|idea\s*\["
    r"|Memory stats:"
    r"|Latency stats:"
    r"|pattern_count.*convergence"
    r"|deep_memory.*active.*fused"
    r"|observation_count"
    r"|ledger.*entries"
    r"|Scheduler:.*tasks"
    r"|SCHED\s*\|"
    r"|worker_heartbeat"
    r"|queue_pressure"
    r"|error_rate"
    r"|Proactive:"
    r"|Reentry:"
    r"|LEARN\].*cycle"
    r"|signals_recorded"
    r"|gravity_fed"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Allowed patterns — these ALWAYS pass through even if they match blocked
# ---------------------------------------------------------------------------

_CRITICAL_PATTERNS = re.compile(
    r"(?:"
    r"CRITICAL"
    r"|URGENTE"
    r"|sistema\s+ca[iy]do"
    r"|system\s+down"
    r"|data\s+loss"
    r"|pérdida\s+de\s+datos"
    r"|requiere\s+aprobación"
    r"|requires?\s+approval"
    r"|authorization\s+required"
    r"|ESCENARIO OPERABLE DETECTADO"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_send_to_user(
    text: str,
    is_user_reply: bool = False,
    reason: str = "",
) -> bool:
    """
    Determine if a message should be sent to the user's Telegram chat.

    Args:
        text: The message content to evaluate.
        is_user_reply: True if this is a direct reply to a user question.
                       User replies ALWAYS pass.
        reason: Optional send reason (e.g. 'user_reply', 'scheduled',
                'proactive', 'digest', 'idea_notification').

    Returns:
        True if the message should be sent to the user.
        False if it should be suppressed (logged only).
    """
    if not text:
        return False

    # User replies always pass
    if is_user_reply:
        return True

    # Explicit user_reply reason always passes
    if reason == "user_reply":
        return True

    # Critical alerts always pass
    if _CRITICAL_PATTERNS.search(text):
        return True

    # Check blocked prefixes
    stripped = text.lstrip()
    for prefix in _BLOCKED_PREFIXES:
        if stripped.startswith(prefix):
            logger.info(
                "TELEGRAM_GUARD blocked (prefix): %s...",
                text[:60].replace("\n", " "),
            )
            return False

    # Check blocked patterns
    if _BLOCKED_PATTERNS.search(text):
        logger.info(
            "TELEGRAM_GUARD blocked (pattern): %s...",
            text[:60].replace("\n", " "),
        )
        return False

    # Known automated send reasons — suppressed by default (silent mode).
    # The user chat should feel like a conversation: only direct replies
    # (is_user_reply / reason='user_reply'), critical alerts, and approval
    # requests reach it automatically. Proactive/scheduled/digest sends are
    # telemetry-like and stay gated here.
    #
    # NOTE: 'reentry' is intentionally ALLOWED. The continuity reentry is a
    # deliberate, single, contextual re-engagement after 12-20h of silence
    # (see core/continuity_reentry.py) — it IS the product behavior we want
    # reaching users, not telemetry. Gating it here silently suppressed every
    # reentry (while _tg_send still returned True), so they never arrived.
    if reason in ("digest", "telemetry", "proactive",
                  "idea_notification", "observation", "metrics",
                  "market_scheduled", "scheduled"):
        logger.info(
            "TELEGRAM_GUARD blocked (reason=%s): %s...",
            reason, text[:60].replace("\n", " "),
        )
        return False

    # Default: allow
    return True


# ---------------------------------------------------------------------------
# Content policy — strip unsolicited internal metrics (rule XbcteaxE)
# ---------------------------------------------------------------------------

# The user is explicitly asking for metrics/stats → the reply may include them.
_METRIC_REQUEST_RE = re.compile(
    r"(?:cu[aá]nt[oa]s?\s+(?:usuarios|interacciones|estrellas|patrones|"
    r"convergencias|constelaciones|equipos|mensajes|se[nñ]ales)"
    r"|m[eé]tricas?|estad[ií]stic|reporte|\bstatus\b|estado\s+del\s+sistema"
    r"|observatorio|dashboard|censo|/vx|/estado|/reporte|/metricas|/auditoria"
    # Preguntas sobre el universo/sistema → las métricas SON la respuesta pedida.
    r"|\bunivers|\bestrellas?\b|\bconvergencias?\b|\bconstelaciones?\b|gravitacional|n[uú]cleo"
    r"|how\s+many\s+(?:users|stars|patterns|interactions)|\bmetrics\b|\bstats\b)",
    re.IGNORECASE,
)

# A clause that leaks an internal count (number + metric noun).
_METRIC_LEAK_RE = re.compile(
    r"[^.!?\n]*\b\d[\d.,]*\s*"
    r"(?:usuarios?|interacci[oó]n(?:es)?|estrellas?|patrones?|convergencias?|"
    r"constelaciones?|equipos?|se[nñ]ales?|utilisateurs?|users?|interactions?|"
    r"stars?|patterns?|convergences?|teams?)\b[^.!?\n]*[.!?]*",
    re.IGNORECASE,
)


def strip_internal_metrics(text: str, user_input: str = "") -> str:
    """Remove unsolicited internal metrics (user/star/pattern counts) from a
    user-facing reply (rule XbcteaxE). If the user explicitly asked for
    metrics/stats/status, the text is returned unchanged. Never returns empty
    (falls back to the original when stripping would blank the reply).
    """
    if not text:
        return text
    if user_input and _METRIC_REQUEST_RE.search(user_input):
        return text  # user asked → allowed
    if not _METRIC_LEAK_RE.search(text):
        return text  # nothing to strip
    cleaned = _METRIC_LEAK_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.!?,;])", r"\1", cleaned)
    return cleaned if cleaned else text
