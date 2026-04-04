"""
Vectrax Temporal Context — Time-aware intelligence.

Vectrax anchors itself to the user's present moment:
  - Injects current date/time into every LLM prompt
  - Resolves stale relative references ("mañana" from yesterday = "hoy")
  - Detects midnight threshold for natural language transitions
  - Never repeats temporal data the user already knows

Usage:
    from vectrax.temporal_context import build_temporal_context
    ctx = build_temporal_context(lang="es")
    # → "[TIEMPO ACTUAL] Viernes 4 de abril de 2026, 13:06. Tarde."

Creado: 2026-04-04
Creador: Mario Bravo Castro (via Oz)
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Day / month names (multilingual)
# ---------------------------------------------------------------------------

_DAYS = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}

_MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio",
           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
}

_PERIODS = {
    "es": {(5, 12): "Mañana", (12, 18): "Tarde", (18, 22): "Noche", (22, 5): "Madrugada"},
    "en": {(5, 12): "Morning", (12, 18): "Afternoon", (18, 22): "Evening", (22, 5): "Night"},
    "fr": {(5, 12): "Matin", (12, 18): "Après-midi", (18, 22): "Soir", (22, 5): "Nuit"},
}


def _get_period(hour: int, lang: str) -> str:
    """Return the period of the day for the given hour."""
    periods = _PERIODS.get(lang, _PERIODS["en"])
    for (start, end), label in periods.items():
        if start <= end:
            if start <= hour < end:
                return label
        else:  # wraps around midnight (22-5)
            if hour >= start or hour < end:
                return label
    return ""


# ---------------------------------------------------------------------------
# Build temporal context string
# ---------------------------------------------------------------------------

def build_temporal_context(lang: str = "es") -> str:
    """
    Build a temporal context block for LLM injection.

    Includes:
      - Current date, day of week, time
      - Period of day (mañana/tarde/noche)
      - Midnight proximity warning if within 30 min
    """
    now = datetime.now()
    hour = now.hour

    days = _DAYS.get(lang, _DAYS["en"])
    months = _MONTHS.get(lang, _MONTHS["en"])
    day_name = days[now.weekday()]
    month_name = months[now.month - 1]
    period = _get_period(hour, lang)
    time_str = now.strftime("%H:%M")

    # Midnight threshold
    midnight_note = ""
    minutes_to_midnight = (24 * 60) - (hour * 60 + now.minute)
    if minutes_to_midnight <= 30:
        if lang == "es":
            midnight_note = " Estamos cerca de la medianoche — 'mañana' es inminente."
        elif lang == "fr":
            midnight_note = " Nous sommes proches de minuit — 'demain' est imminent."
        else:
            midnight_note = " We're close to midnight — 'tomorrow' is imminent."

    if lang == "es":
        date_str = f"{day_name.capitalize()} {now.day} de {month_name} de {now.year}"
        return (
            f"[TIEMPO ACTUAL]\n"
            f"{date_str}, {time_str}. {period}.{midnight_note}\n"
            f"Usa esta referencia temporal para todas tus respuestas. "
            f"No digas 'mañana' si ya es hoy. No repitas datos temporales que el usuario ya sabe."
        )
    elif lang == "fr":
        date_str = f"{day_name.capitalize()} {now.day} {month_name} {now.year}"
        return (
            f"[TEMPS ACTUEL]\n"
            f"{date_str}, {time_str}. {period}.{midnight_note}\n"
            f"Utilise cette référence temporelle pour toutes tes réponses. "
            f"Ne dis pas 'demain' si c'est déjà aujourd'hui."
        )
    else:
        date_str = f"{day_name} {month_name} {now.day}, {now.year}"
        return (
            f"[CURRENT TIME]\n"
            f"{date_str}, {time_str}. {period}.{midnight_note}\n"
            f"Use this time reference for all your responses. "
            f"Don't say 'tomorrow' if it's already today. Don't repeat temporal data the user already knows."
        )


# ---------------------------------------------------------------------------
# Resolve stale temporal references in text
# ---------------------------------------------------------------------------

_RELATIVE_TIME_ES = {
    re.compile(r"\bmañana\b", re.IGNORECASE): lambda now: "hoy" if _is_stale_tomorrow(now) else None,
    re.compile(r"\bayer\b", re.IGNORECASE): lambda now: "anteayer" if _is_stale_yesterday(now) else None,
    re.compile(r"\bhoy\b", re.IGNORECASE): lambda now: "ayer" if _is_stale_today(now) else None,
}

_RELATIVE_TIME_EN = {
    re.compile(r"\btomorrow\b", re.IGNORECASE): lambda now: "today" if _is_stale_tomorrow(now) else None,
    re.compile(r"\byesterday\b", re.IGNORECASE): lambda now: "the day before" if _is_stale_yesterday(now) else None,
}


def _is_stale_tomorrow(now: datetime) -> bool:
    """A 'tomorrow' reference from yesterday is now 'today'."""
    return False  # This is checked with pattern timestamp


def _is_stale_yesterday(now: datetime) -> bool:
    return False


def _is_stale_today(now: datetime) -> bool:
    return False


def resolve_temporal_references(
    text: str,
    pattern_timestamp: float,
    lang: str = "es",
) -> str:
    """
    Resolve stale relative time references in pattern text.

    If a pattern says "mañana tengo reunión" and it was written yesterday,
    this converts it to "hoy tengo reunión".
    """
    now = datetime.now()
    pattern_dt = datetime.fromtimestamp(pattern_timestamp)
    days_ago = (now.date() - pattern_dt.date()).days

    if days_ago <= 0:
        return text  # Same day — no adjustment needed

    result = text

    if lang in ("es",):
        if days_ago == 1:
            # "mañana" from yesterday → "hoy"
            result = re.sub(r"\bmañana\b", "hoy", result, flags=re.IGNORECASE)
            # "hoy" from yesterday → "ayer"
            result = re.sub(r"\bhoy\b", "ayer", result, flags=re.IGNORECASE)
        elif days_ago == 2:
            result = re.sub(r"\bmañana\b", "ayer", result, flags=re.IGNORECASE)
            result = re.sub(r"\bhoy\b", "anteayer", result, flags=re.IGNORECASE)
        elif days_ago > 2:
            day_label = f"hace {days_ago} días"
            result = re.sub(r"\bmañana\b", day_label, result, flags=re.IGNORECASE)
            result = re.sub(r"\bhoy\b", day_label, result, flags=re.IGNORECASE)
    elif lang in ("en",):
        if days_ago == 1:
            result = re.sub(r"\btomorrow\b", "today", result, flags=re.IGNORECASE)
            result = re.sub(r"\btoday\b", "yesterday", result, flags=re.IGNORECASE)
        elif days_ago > 1:
            day_label = f"{days_ago} days ago"
            result = re.sub(r"\btomorrow\b", day_label, result, flags=re.IGNORECASE)
            result = re.sub(r"\btoday\b", day_label, result, flags=re.IGNORECASE)
    elif lang in ("fr",):
        if days_ago == 1:
            result = re.sub(r"\bdemain\b", "aujourd'hui", result, flags=re.IGNORECASE)
            result = re.sub(r"\baujourd'hui\b", "hier", result, flags=re.IGNORECASE)
        elif days_ago > 1:
            day_label = f"il y a {days_ago} jours"
            result = re.sub(r"\bdemain\b", day_label, result, flags=re.IGNORECASE)

    return result


# ---------------------------------------------------------------------------
# Session echo filter
# ---------------------------------------------------------------------------

_session_facts: dict = {}  # user_id → set of fact hashes


def register_user_fact(user_id: str, fact: str) -> None:
    """Register a fact the user mentioned in this session."""
    key = fact.strip().lower()[:100]
    if user_id not in _session_facts:
        _session_facts[user_id] = set()
    _session_facts[user_id].add(key)


def is_already_known(user_id: str, fact: str) -> bool:
    """Check if this fact was already mentioned by the user this session."""
    key = fact.strip().lower()[:100]
    return key in _session_facts.get(user_id, set())


def filter_echo_from_context(
    user_id: str,
    context_lines: list,
) -> list:
    """Remove lines from memory context that the user already said this session."""
    if user_id not in _session_facts or not _session_facts[user_id]:
        return context_lines

    known = _session_facts[user_id]
    filtered = []
    for line in context_lines:
        key = line.strip().lower()[:100]
        if key not in known:
            filtered.append(line)
    return filtered


def clear_session(user_id: str) -> None:
    """Clear session facts (on session end or timeout)."""
    _session_facts.pop(user_id, None)
