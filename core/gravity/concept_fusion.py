"""
core/gravity/concept_fusion.py — Fusión de conceptos repetidos en
identidades contextuales.

Cuando 3+ recuerdos del mismo user comparten la misma combinación
(persona, lugar) — codificada como `identity_key = "Naomy|Casa"` —
los condensamos en una única ContextIdentity. Las evidencias originales
NUNCA se borran: se marcan `fused_into = identity.id`. La identidad
acumula `identity_mass` por frecuencia y mantiene la `dominant_emotion`
de las evidencias.

API:
    ConceptFusion(store, threshold=3)
    .find_similar_concepts(user_id, record) -> list[record]
    .fuse_if_threshold_met(user_id, record) -> Optional[dict]
        Devuelve la ContextIdentity creada o actualizada (None si no
        alcanzó el threshold).
    .create_or_update_context_identity(user_id, key, evidences,
                                       people, location, emotion)
        -> dict (ContextIdentity persistida)

Convenciones de tags (compartidas con governor):
    "person:<Name>"
    "location:<Place>"
    "emotion:<kind>"
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.gravity.fusion")


_MASS_PER_EVIDENCE = 4.0   # cada evidencia suma 4 a la identity_mass


# ===========================================================================
# Helpers de extracción de identidad desde tags
# ===========================================================================

def _extract_person(tags: List[str]) -> str:
    for t in tags:
        if t.startswith("person:"):
            v = t.split(":", 1)[1].strip()
            if v:
                return v
    return ""


def _extract_location(tags: List[str]) -> str:
    for t in tags:
        if t.startswith("location:"):
            v = t.split(":", 1)[1].strip()
            if v:
                return v
    return ""


def _extract_emotion(tags: List[str]) -> str:
    for t in tags:
        if t.startswith("emotion:"):
            v = t.split(":", 1)[1].strip()
            if v:
                return v
    return ""


def derive_identity_key(record: Dict[str, Any]) -> str:
    """Deriva el identity_key 'Persona|Lugar' a partir de los tags.

    Devuelve cadena vacía si falta persona O lugar (no fusionable).
    """
    tags = [str(t) for t in (record.get("tags") or [])]
    person = _extract_person(tags)
    location = _extract_location(tags)
    if not person or not location:
        return ""
    return f"{person}|{location}"


# ===========================================================================
# ConceptFusion
# ===========================================================================

class ConceptFusion:
    """Fusiona recuerdos repetidos en ContextIdentity persistente."""

    def __init__(self, store, threshold: int = 3) -> None:
        self.store = store
        self.threshold = max(2, int(threshold))

    # -------------------------------------------------------- find_similar
    def find_similar_concepts(
        self,
        user_id: str,
        record: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Devuelve la lista de records ACTIVE del user con la MISMA
        identity_key que `record`. La lista incluye al propio record.
        Filtrada estrictamente por user_id en SQL.
        """
        if not user_id:
            return []
        key = derive_identity_key(record)
        if not key:
            return []
        out: List[Dict[str, Any]] = []
        for r in self.store.list_active_for_user(user_id, limit=2000):
            if derive_identity_key(r) == key:
                out.append(r)
        return out

    # ------------------------------------------------------ fuse_if_threshold
    def fuse_if_threshold_met(
        self,
        user_id: str,
        record: Dict[str, Any],
        threshold: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Si hay >= threshold evidencias con la misma identity_key,
        crea/actualiza la ContextIdentity y marca `fused_into` en cada
        evidencia. Devuelve la ContextIdentity persistida (dict) o None
        si no se alcanzó el threshold.
        """
        thr = max(2, int(threshold)) if threshold else self.threshold
        evidences = self.find_similar_concepts(user_id, record)
        if len(evidences) < thr:
            return None

        key = derive_identity_key(record)
        people = sorted({_extract_person(r.get("tags") or []) for r in evidences} - {""})
        locations = sorted({_extract_location(r.get("tags") or []) for r in evidences} - {""})
        location = locations[0] if locations else ""

        # Emoción dominante: la más frecuente entre las evidencias
        emotion_counter: Counter = Counter()
        for r in evidences:
            emo = _extract_emotion(r.get("tags") or [])
            if emo:
                emotion_counter[emo] += 1
        dominant_emotion = emotion_counter.most_common(1)[0][0] if emotion_counter else ""

        return self.create_or_update_context_identity(
            user_id=user_id,
            identity_key=key,
            evidences=evidences,
            people=people,
            location=location,
            dominant_emotion=dominant_emotion,
        )

    # ---------------------------------------- create_or_update_context_identity
    def create_or_update_context_identity(
        self,
        user_id: str,
        identity_key: str,
        evidences: List[Dict[str, Any]],
        people: List[str],
        location: str,
        dominant_emotion: str = "",
    ) -> Dict[str, Any]:
        """Crea o actualiza la ContextIdentity. Marca cada evidencia
        con fused_into = identity.id. NUNCA borra evidencia.
        """
        existing = self.store.get_context_identity_by_key(
            user_id, identity_key,
        )
        evidence_ids = [e["id"] for e in evidences]
        evidence_count = len(evidence_ids)
        identity_mass = float(evidence_count) * _MASS_PER_EVIDENCE
        summary = self._build_summary(people, location, evidence_count)

        # Reusa el id existente si ya hay identidad
        ident_payload = {
            "id": existing["id"] if existing else None,
            "user_id": user_id,
            "identity_key": identity_key,
            "people": people,
            "location": location,
            "dominant_emotion": dominant_emotion or (
                existing["dominant_emotion"] if existing else ""
            ),
            "evidence_ids": evidence_ids,
            "evidence_count": evidence_count,
            "identity_mass": identity_mass,
            "summary": summary,
            "first_seen": existing["first_seen"] if existing else "",
            "created_at": existing["created_at"] if existing else "",
        }
        ident_id = self.store.upsert_context_identity(ident_payload)

        # Marcar evidencias como fusionadas (preservadas, no borradas)
        for ev in evidences:
            self.store.set_fused_into(ev["id"], ident_id)

        logger.info(
            "fusion: identity %s = %r | mass=%.1f | evidences=%d",
            ident_id, identity_key, identity_mass, evidence_count,
        )
        # Devolver el record fresco con el id correcto
        return self.store.get_context_identity_by_key(user_id, identity_key) or {}

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _build_summary(people: List[str], location: str, count: int) -> str:
        if not people and not location:
            return f"Patrón recurrente ({count} evidencias)"
        people_str = ", ".join(people) if people else "alguien"
        if location:
            return f"{people_str} en {location} ({count} evidencias)"
        return f"{people_str} ({count} evidencias)"
