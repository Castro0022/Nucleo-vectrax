"""
core/gravity/governor.py — Memory Governor.

Decide qué memorias merecen DEEP, cuáles deben degradarse y cómo se
calcula la gravedad. Es el árbitro entre la entrada bruta y la
memoria significativa: previene la entropía sin perder identidad.

Convenciones de tags (acordadas con concept_fusion):
    "person:<Name>"      ej. "person:Naomy"          → Persona
    "location:<Place>"   ej. "location:Casa"          → Lugar
    "emotion:<kind>"     ej. "emotion:joy"            → Emoción
    "preference"         marca explícita de gusto/disgusto
    "principle"          marca de principio operativo
    "decision"           decisión importante del user
    "pattern"            patrón recurrente detectado
    "noise"              marca explícita de ruido (saludo/ack)

Reglas:

  PROMOTE → memory_type = "DEEP", gravity_score alto, status ACTIVE
    - tag "person:*"
    - tag "preference"
    - tag "principle"
    - tag "emotion:*" con peso fuerte (joy/anger/grief/love/fear)
    - tag "location:*" recurrente (>=3 hits)
    - tag "decision"
    - tag "pattern"
    - retrieval_count >= 3

  NO PROMOTE → memory_status = "COLD" (apta para decay)
    - texto trivial (saludo / ack / ok / gracias)
    - duplicado exacto de algo activo del user
    - confidence < 0.4 o sin entidad relevante

API:
    MemoryGovernor(store)
    .evaluate_memory(record)        -> dict con plan completo
    .should_promote(record)         -> bool
    .should_decay(record)           -> bool
    .calculate_gravity_score(record)-> float
    .promote_warm_to_deep(user_id)  -> int (records promovidos)
    .demote_noise(user_id)          -> int (records demovidos)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.gravity.governor")


# ===========================================================================
# Configuración declarativa
# ===========================================================================

# Patrones de "ruido": saludos, acks, agradecimientos. Match contra
# raw_text normalizado (lower, sin signos finales).
_NOISE_PATTERNS = re.compile(
    r"^\s*(?:"
    r"hola|hey|hi|hello|holi|buenas|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches"
    r"|gracias|thanks|thank\s+you|merci|grazie|danke"
    r"|ok|okay|listo|perfecto|de\s+acuerdo|dale|vale|sure|got\s+it|copy"
    r"|s[ií]|si|no|claro|por\s+supuesto"
    r"|adios|adiós|chao|bye|see\s+you"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Emociones fuertes que justifican promoción.
_STRONG_EMOTIONS = frozenset({
    "joy", "anger", "grief", "love", "fear", "sadness",
    "alegría", "alegria", "ira", "duelo", "amor", "miedo", "tristeza",
    "crisis", "passion", "pasión", "pasion",
})

# Memory types que NO se degradan jamás (contrato con decay.py).
PROTECTED_MEMORY_TYPES = frozenset({
    "DEEP", "PERSON_RECOGNIZED", "PRINCIPLE", "DECISION",
})

# Pesos para calculate_gravity_score (0..N).
_BASE_SCORE = 1.0
_W_PERSON = 3.0
_W_PREFERENCE = 2.5
_W_PRINCIPLE = 3.5
_W_EMOTION_STRONG = 2.5
_W_LOCATION_RECURRENT = 1.5
_W_DECISION = 2.5
_W_PATTERN = 1.5
_W_RETRIEVAL = 0.4    # por hit acumulado (capped)
_W_RETRIEVAL_CAP = 4.0
_W_MASS = 0.05        # por punto de masa (capped)
_W_MASS_CAP = 3.0
_W_NOISE_PENALTY = -2.0


# ===========================================================================
# Helpers
# ===========================================================================

def _tags_of(record: Dict[str, Any]) -> List[str]:
    return [str(t) for t in (record.get("tags") or [])]


def _has_tag_prefix(tags: List[str], prefix: str) -> bool:
    return any(t.startswith(prefix) for t in tags)


def _has_strong_emotion(tags: List[str]) -> bool:
    for t in tags:
        if t.startswith("emotion:"):
            kind = t.split(":", 1)[1].strip().lower()
            if kind in _STRONG_EMOTIONS:
                return True
    return False


def _is_noise_text(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    if len(cleaned) < 2:
        return True
    return bool(_NOISE_PATTERNS.match(cleaned))


def _location_count_for_user(store, user_id: str, location: str) -> int:
    """Cuenta cuántos records ACTIVE del user mencionan esta location."""
    if not user_id or not location:
        return 0
    tag_token = f"location:{location}".lower()
    n = 0
    for r in store.list_active_for_user(user_id, limit=2000):
        for t in r.get("tags") or []:
            if t.lower() == tag_token:
                n += 1
                break
    return n


# ===========================================================================
# Governor
# ===========================================================================

class MemoryGovernor:
    """Árbitro entre entrada bruta y memoria significativa."""

    def __init__(self, store) -> None:
        self.store = store

    # ----------------------------------------------------------- evaluate
    def evaluate_memory(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Devuelve un plan completo: should_promote, should_decay,
        gravity_score recomendado, memory_type sugerido y razón.
        """
        tags = _tags_of(record)
        text = record.get("raw_text") or ""
        promote, reason_promote = self._promote_decision(record, tags, text)
        decay = self._decay_decision(record, tags, text, promote)
        score = self._score(record, tags, text)
        m_type = self._suggest_memory_type(tags, promote)
        return {
            "record_id": record.get("id"),
            "should_promote": promote,
            "should_decay": decay,
            "gravity_score": score,
            "memory_type": m_type,
            "reason": reason_promote,
        }

    # ------------------------------------------------------ public flags
    def should_promote(self, record: Dict[str, Any]) -> bool:
        tags = _tags_of(record)
        text = record.get("raw_text") or ""
        promote, _ = self._promote_decision(record, tags, text)
        return promote

    def should_decay(self, record: Dict[str, Any]) -> bool:
        tags = _tags_of(record)
        text = record.get("raw_text") or ""
        promote, _ = self._promote_decision(record, tags, text)
        return self._decay_decision(record, tags, text, promote)

    def calculate_gravity_score(self, record: Dict[str, Any]) -> float:
        tags = _tags_of(record)
        text = record.get("raw_text") or ""
        return self._score(record, tags, text)

    # --------------------------------------------------------- batch ops
    def promote_warm_to_deep(self, user_id: str) -> int:
        """Recorre records ACTIVE del user; marca como DEEP los que
        deben promoverse y actualiza su gravity_score. Devuelve el
        número de records promovidos.
        """
        if not user_id:
            return 0
        n = 0
        for record in self.store.list_active_for_user(user_id, limit=2000):
            plan = self.evaluate_memory(record)
            if plan["should_promote"] and record.get("memory_type") != "DEEP":
                self.store.set_memory_type(record["id"], plan["memory_type"])
                self.store.set_gravity_score(record["id"], plan["gravity_score"])
                n += 1
                logger.info(
                    "governor: promoted %s → %s (reason=%s)",
                    record["id"], plan["memory_type"], plan["reason"],
                )
        return n

    def demote_noise(self, user_id: str) -> int:
        """Marca como COLD los records ruidosos. Devuelve el número de
        records demovidos. NO toca PROTECTED_MEMORY_TYPES.
        """
        if not user_id:
            return 0
        n = 0
        for record in self.store.list_active_for_user(user_id, limit=2000):
            if record.get("memory_type") in PROTECTED_MEMORY_TYPES:
                continue
            tags = _tags_of(record)
            text = record.get("raw_text") or ""
            promote, _ = self._promote_decision(record, tags, text)
            if promote:
                continue
            if _is_noise_text(text) or self._is_low_signal(record, tags, text):
                self.store.set_status(record["id"], "COLD")
                # Penaliza la gravedad
                self.store.add_gravity_delta(record["id"], _W_NOISE_PENALTY)
                n += 1
                logger.info("governor: demoted %s as noise", record["id"])
        return n

    # ------------------------------------------------------------ logic
    def _promote_decision(
        self,
        record: Dict[str, Any],
        tags: List[str],
        text: str,
    ) -> tuple:
        """Devuelve (bool, reason)."""
        # Lista negra primero — ahorra cómputo
        if _is_noise_text(text):
            return False, "noise_text"
        if self._is_duplicate_of_active(record):
            return False, "duplicate"
        if self._is_low_signal(record, tags, text):
            return False, "low_signal"

        # Reglas de promoción
        if _has_tag_prefix(tags, "person:"):
            return True, "person_recognized"
        if "preference" in tags:
            return True, "preference"
        if "principle" in tags:
            return True, "principle"
        if "decision" in tags:
            return True, "decision"
        if "pattern" in tags:
            return True, "pattern"
        if _has_strong_emotion(tags):
            return True, "strong_emotion"
        if int(record.get("retrieval_count") or 0) >= 3:
            return True, "high_retrieval"
        # Lugar recurrente (>=3 hits del mismo location en records ACTIVE)
        for t in tags:
            if t.startswith("location:"):
                loc = t.split(":", 1)[1].strip()
                if loc and _location_count_for_user(
                    self.store, record.get("user_id", ""), loc,
                ) >= 3:
                    return True, "recurrent_location"
        return False, "no_signal"

    def _decay_decision(
        self,
        record: Dict[str, Any],
        tags: List[str],
        text: str,
        promote: bool,
    ) -> bool:
        if record.get("memory_type") in PROTECTED_MEMORY_TYPES:
            return False
        if _has_tag_prefix(tags, "person:"):
            return False
        if "principle" in tags or "decision" in tags:
            return False
        if promote:
            return False
        # Si el record ya está en COLD, sí aplica decay
        if record.get("memory_status") == "COLD":
            return True
        # Si es noise puro y aún ACTIVE, también
        if _is_noise_text(text):
            return True
        return False

    def _score(
        self,
        record: Dict[str, Any],
        tags: List[str],
        text: str,
    ) -> float:
        score = _BASE_SCORE
        if _has_tag_prefix(tags, "person:"):
            score += _W_PERSON
        if "preference" in tags:
            score += _W_PREFERENCE
        if "principle" in tags:
            score += _W_PRINCIPLE
        if _has_strong_emotion(tags):
            score += _W_EMOTION_STRONG
        if "decision" in tags:
            score += _W_DECISION
        if "pattern" in tags:
            score += _W_PATTERN
        # location recurrente
        for t in tags:
            if t.startswith("location:"):
                loc = t.split(":", 1)[1].strip()
                if loc and _location_count_for_user(
                    self.store, record.get("user_id", ""), loc,
                ) >= 3:
                    score += _W_LOCATION_RECURRENT
                    break
        # retrieval_count (capped)
        rc = float(record.get("retrieval_count") or 0)
        score += min(rc * _W_RETRIEVAL, _W_RETRIEVAL_CAP)
        # mass acumulada del mass_tracker (capped)
        mass = float(record.get("mass") or 0)
        score += min(mass * _W_MASS, _W_MASS_CAP)
        # penalty
        if _is_noise_text(text):
            score += _W_NOISE_PENALTY
        return max(0.0, round(score, 4))

    def _suggest_memory_type(self, tags: List[str], promote: bool) -> str:
        if not promote:
            return "episode"
        if _has_tag_prefix(tags, "person:"):
            return "PERSON_RECOGNIZED"
        if "principle" in tags:
            return "PRINCIPLE"
        if "decision" in tags:
            return "DECISION"
        return "DEEP"

    def _is_low_signal(
        self,
        record: Dict[str, Any],
        tags: List[str],
        text: str,
    ) -> bool:
        # Sin entidades relevantes Y texto cortísimo
        if not tags and len(text.strip()) < 10:
            return True
        return False

    def _is_duplicate_of_active(self, record: Dict[str, Any]) -> bool:
        """Duplicado exacto del raw_text en otro record ACTIVE del mismo user."""
        text = (record.get("raw_text") or "").strip()
        uid = record.get("user_id", "")
        rid = record.get("id", "")
        if not text or not uid:
            return False
        try:
            for r in self.store.list_active_for_user(uid, limit=200):
                if r.get("id") == rid:
                    continue
                if (r.get("raw_text") or "").strip() == text:
                    return True
        except Exception:
            pass
        return False
