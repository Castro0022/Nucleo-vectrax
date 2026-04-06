"""
Vectrax Memory Evaluator — decides what deserves to be remembered.

Every input is evaluated as potential memory. The system decides
automatically what to store, what to keep as candidate, and what
to discard. The user never has to say "remember this".

Scoring (0.0 – 1.0):
  0.8+ → STORED    (confirmed valuable — affects centroid)
  0.4+ → CANDIDATE (potentially valuable — pending resolution)
  <0.4 → DISCARDED (trivial — kept for audit, ignored by centroid)

Signals that increase value:
  - Contains a recoverable fact (name, date, number, place)
  - Contains personal data (age, job, preference, interest)
  - Contains an intention or plan ("mañana tengo", "quiero hacer")
  - Is longer than 5 words with real content
  - References a specific entity (person, tool, project)
  - Is a recurrence (user said something similar before)

Signals that decrease value:
  - Pure greeting or farewell
  - Single-word confirmation (ok, sí, vale)
  - Meta-communication about the system ("habla en español")
  - Question with no assertion (questions activate star, not memory)
  - Very short with no fact (<4 words, no numbers, no names)

Creado: 2026-04-06
Creador: Mario Bravo Castro (via Oz)
"""

from __future__ import annotations

import re
from typing import Tuple

from vectrax.models import PATTERN_STORED, PATTERN_CANDIDATE, PATTERN_DISCARDED

# ---------------------------------------------------------------------------
# Value signals (regex-based, fast, no LLM)
# ---------------------------------------------------------------------------

# Facts: names, numbers, dates, places, emails, URLs
_FACT_SIGNALS = re.compile(
    r"(?:"
    r"\b\d{1,2}[:/]\d{2}\b"             # time: 10:30, 3:00
    r"|\b\d{1,2}\s+(?:am|pm|AM|PM)\b"   # 10 AM
    r"|\b\d{1,2}\s+(?:de\s+)?\w+\s+(?:de\s+)?\d{4}\b"  # 5 de abril de 2026
    r"|\b\d+\s*(?:años|days|meses|months|kg|lb|km|mi|USD|EUR|\$|€)\b"  # quantities
    r"|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"  # proper names (2+ words capitalized)
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"         # email
    r"|https?://\S+"                     # URL
    r"|\b\d{3,}\b"                       # numbers with 3+ digits (phone, price, etc.)
    r")",
    re.UNICODE,
)

# Personal data: identity, preferences, relationships
_PERSONAL_SIGNALS = re.compile(
    r"(?:"
    r"(?:me llamo|mi nombre es|soy\s+\w+|my name is|i'm\s+\w+)"
    r"|(?:tengo\s+\d+\s+a[ñn]os|i'm\s+\d+\s+years)"
    r"|(?:vivo en|trabajo en|mi empresa|mi negocio|my company)"
    r"|(?:mi (?:novia|esposa|esposo|pareja|hijo|hija|familia|hermano|madre|padre))"
    r"|(?:me gusta|prefiero|me interesa|estoy aprendiendo|I (?:like|prefer|work))"
    r"|(?:mi correo|mi email|mi teléfono|mi dirección)"
    r")",
    re.IGNORECASE,
)

# Intentions and plans
_INTENTION_SIGNALS = re.compile(
    r"(?:"
    r"(?:mañana\s+(?:tengo|voy|necesito|quiero))"
    r"|(?:(?:voy|quiero|necesito|planeo|debo)\s+(?:a\s+)?(?:\w+\s+){0,3})"
    r"|(?:tengo\s+(?:reunión|cita|junta|examen|entrevista|vuelo|viaje))"
    r"|(?:(?:tomorrow|next\s+week|tonight)\s+(?:I\s+)?(?:have|need|want|will))"
    r"|(?:recordar|remind|no olvidar|don't forget)"
    r"|(?:pendiente|deadline|fecha límite|due date)"
    r")",
    re.IGNORECASE,
)

# Trivial / low value
_TRIVIAL_PATTERNS = re.compile(
    r"^(?:"
    r"(?:hola|hi|hey|hello|buenas?|buenos?)\s*(?:vectrax)?\s*$"
    r"|(?:ok|sí|si|no|vale|bien|bueno|claro|perfecto|listo|entendido)\s*$"
    r"|(?:gracias|thanks|merci|danke)\s*$"
    r"|(?:bye|chao|adiós|nos vemos|hasta luego)\s*$"
    r"|(?:como estas|how are you|comment ça va)\s*[?]?\s*$"
    r"|(?:que tal|qué tal)\s*[?]?\s*$"
    r")",
    re.IGNORECASE,
)

# Questions (activate star but don't store as memory)
_PURE_QUESTION = re.compile(
    r"^(?:qu[eé]|c[oó]mo|cu[aá]l|cu[aá]ndo|d[oó]nde|cu[aá]nto|qui[eé]n"
    r"|what|who|where|when|why|how|which)\b.*[?]?\s*$",
    re.IGNORECASE,
)

# System/meta commands
_META_PATTERNS = re.compile(
    r"(?:"
    r"(?:h[aá]blame|resp[oó]nde)\s+en\s+\w+"
    r"|(?:switch|change)\s+(?:to|language)"
    r"|/\w+"  # any command
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

def evaluate_memory_value(text: str, user_id: str = "") -> Tuple[str, float]:
    """
    Evaluate the memory value of a text.

    Returns:
        (status, value_score) where:
        - status: 'stored' | 'candidate' | 'discarded'
        - value_score: 0.0 – 1.0
    """
    text = text.strip()
    if not text:
        return PATTERN_DISCARDED, 0.0

    text_lower = text.lower().rstrip("!?.")
    word_count = len(text.split())

    # ── Immediate discard: trivial ────────────────────────────
    if _TRIVIAL_PATTERNS.match(text_lower):
        return PATTERN_DISCARDED, 0.05

    # ── Immediate discard: system/meta commands ───────────────
    if _META_PATTERNS.match(text):
        return PATTERN_DISCARDED, 0.1

    # ── Score calculation ─────────────────────────────────────
    score = 0.0

    # Length bonus (longer = more likely to contain value)
    if word_count >= 10:
        score += 0.25
    elif word_count >= 5:
        score += 0.15
    elif word_count >= 3:
        score += 0.05

    # Fact signals (numbers, dates, names, emails)
    fact_count = len(_FACT_SIGNALS.findall(text))
    if fact_count >= 2:
        score += 0.35
    elif fact_count >= 1:
        score += 0.20

    # Personal data (strong signal — always worth remembering)
    if _PERSONAL_SIGNALS.search(text):
        score += 0.40

    # Intention / plan
    if _INTENTION_SIGNALS.search(text):
        score += 0.25

    # Pure question penalty (questions activate, don't store)
    if _PURE_QUESTION.match(text):
        score *= 0.3  # reduce but don't zero — question topics still have value

    # Very short with no signals
    if word_count <= 3 and score < 0.2:
        return PATTERN_DISCARDED, max(score, 0.05)

    # Clamp
    score = min(score, 1.0)

    # ── Classification ────────────────────────────────────────
    if score >= 0.4:
        return PATTERN_STORED, round(score, 4)
    elif score >= 0.15:
        return PATTERN_CANDIDATE, round(score, 4)
    else:
        return PATTERN_DISCARDED, round(score, 4)
