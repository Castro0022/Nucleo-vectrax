"""
core/gravity/essential_summary.py — Destilación de sabiduría persistente.

A partir de los records antiguos del user, extrae principios reusables
y los persiste como `essential_memories`. Los principios sobreviven al
decay y aparecen en cualquier retrieval de identidad.

Ejemplos de principios destilables:
  - "A Mario le gusta el café fuerte."           (preference recurrente)
  - "Mario prefiere respuestas directas."        (principle declarado)
  - "Naomy aparece como vínculo recurrente."     (person frecuente)
  - "Mario evita largos análisis."               (pattern detectado)

Convenciones de tags (compartidas con governor / concept_fusion):
    "person:<Name>"
    "preference"
    "principle"
    "pattern"

API:
    EssentialSummaryEngine(store, user_name=None)
    .summarize_old_threads(user_id, before_date=None) -> list[dict]
        Procesa los records previos a `before_date` y genera principios.
    .extract_user_principles(records) -> list[dict]
        Versión funcional pura (sin tocar el store): dada una lista
        de records, devuelve principios candidatos.
    .persist_essential_summary(user_id, principles) -> list[str] (ids)

Cada principio persistido incluye:
    type, summary, evidence_ids, confidence, gravity_score
"""

from __future__ import annotations

import datetime
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.gravity.essential")


# ===========================================================================
# Heurísticas de destilación
# ===========================================================================

# Mínimo de evidencias para considerar algo recurrente
_MIN_EVIDENCES_PERSON = 3
_MIN_EVIDENCES_PREFERENCE = 2
_MIN_EVIDENCES_PATTERN = 3

# Confianza por tipo de principio
_CONF_BY_TYPE = {
    "principle": 0.95,   # declarado explícitamente
    "preference": 0.85,
    "person": 0.80,
    "pattern": 0.70,
}


def _tags_of(record: Dict[str, Any]) -> List[str]:
    return [str(t) for t in (record.get("tags") or [])]


def _parse_iso(ts: str) -> Optional[datetime.datetime]:
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.rstrip("Z"))
    except Exception:
        return None


def _ts_dt(ts: float) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.utcfromtimestamp(float(ts))
    except Exception:
        return None


# ===========================================================================
# EssentialSummaryEngine
# ===========================================================================

class EssentialSummaryEngine:
    """Destilador de principios persistentes."""

    def __init__(self, store, user_name: Optional[str] = None) -> None:
        self.store = store
        self.user_name = (user_name or "Mario").strip()

    # ------------------------------------------------------- summarize old
    def summarize_old_threads(
        self,
        user_id: str,
        before_date: Optional[datetime.datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Recorre los records del user; si `before_date` se da, solo
        considera los anteriores. Extrae principios y los persiste.

        Devuelve la lista de principios persistidos (con ids).
        """
        if not user_id:
            return []
        records = self.store.list_all_for_user(user_id, limit=5000)
        if before_date is not None:
            records = [
                r for r in records
                if (_ts_dt(r.get("ts")) or datetime.datetime.utcnow())
                < before_date
            ]
        principles = self.extract_user_principles(records)
        ids = self.persist_essential_summary(user_id, principles)
        # Re-hidratar con los ids reales
        return [
            {**p, "id": ids[i]}
            for i, p in enumerate(principles)
            if i < len(ids)
        ]

    # ------------------------------------------ extract_user_principles
    def extract_user_principles(
        self,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Funcional puro: dada una lista de records, devuelve la lista
        de principios candidatos. NO toca el store. Cada principio es:
            {type, summary, evidence_ids, confidence}
        """
        if not records:
            return []
        principles: List[Dict[str, Any]] = []

        # 1) Personas recurrentes → "Naomy aparece como vínculo recurrente"
        person_ev: Dict[str, List[str]] = {}
        for r in records:
            for t in _tags_of(r):
                if t.startswith("person:"):
                    name = t.split(":", 1)[1].strip()
                    if not name:
                        continue
                    person_ev.setdefault(name, []).append(r["id"])
        for name, ev_ids in person_ev.items():
            if len(ev_ids) >= _MIN_EVIDENCES_PERSON:
                principles.append({
                    "type": "person",
                    "summary": f"{name} aparece como vínculo recurrente.",
                    "evidence_ids": ev_ids,
                    "confidence": _CONF_BY_TYPE["person"],
                })

        # 2) Preferencias declaradas → "A {user} le gusta X"
        pref_ev: List[Dict[str, Any]] = []
        for r in records:
            tags = _tags_of(r)
            if "preference" in tags:
                pref_ev.append(r)
        # Agrupa por tópico (extracción simple por tag adicional o palabra)
        topic_ev: Dict[str, List[str]] = {}
        for r in pref_ev:
            topic = self._extract_preference_topic(r)
            if topic:
                topic_ev.setdefault(topic, []).append(r["id"])
        for topic, ev_ids in topic_ev.items():
            if len(ev_ids) >= _MIN_EVIDENCES_PREFERENCE:
                principles.append({
                    "type": "preference",
                    "summary": f"A {self.user_name} le gusta {topic}.",
                    "evidence_ids": ev_ids,
                    "confidence": _CONF_BY_TYPE["preference"],
                })

        # 3) Principios declarados explícitamente → "Mario prefiere X"
        for r in records:
            tags = _tags_of(r)
            if "principle" in tags:
                # Usa el resumen del propio record si existe
                summary = (r.get("summary") or r.get("raw_text") or "").strip()
                if not summary:
                    continue
                # Normaliza a una línea
                line = summary.split("\n")[0][:140]
                principles.append({
                    "type": "principle",
                    "summary": self._principle_phrase(line),
                    "evidence_ids": [r["id"]],
                    "confidence": _CONF_BY_TYPE["principle"],
                })

        # 4) Patrones recurrentes → tag 'pattern'
        pattern_groups: Dict[str, List[str]] = {}
        for r in records:
            tags = _tags_of(r)
            if "pattern" in tags:
                topic = self._extract_pattern_topic(r)
                if topic:
                    pattern_groups.setdefault(topic, []).append(r["id"])
        for topic, ev_ids in pattern_groups.items():
            if len(ev_ids) >= _MIN_EVIDENCES_PATTERN:
                principles.append({
                    "type": "pattern",
                    "summary": f"{self.user_name} {topic} de forma recurrente.",
                    "evidence_ids": ev_ids,
                    "confidence": _CONF_BY_TYPE["pattern"],
                })

        return principles

    # ---------------------------------------------- persist_essential_summary
    def persist_essential_summary(
        self,
        user_id: str,
        principles: List[Dict[str, Any]],
    ) -> List[str]:
        """Persiste los principios en `essential_memories`. Devuelve ids."""
        if not user_id or not principles:
            return []
        ids: List[str] = []
        for p in principles:
            ess_id = self.store.upsert_essential_memory({
                "user_id": user_id,
                "type": p.get("type", "principle"),
                "summary": p["summary"],
                "evidence_ids": p.get("evidence_ids") or [],
                "confidence": p.get("confidence", 0.0),
                # gravity_score alto: los principios sobreviven al decay
                "gravity_score": 5.0 + p.get("confidence", 0.0) * 5.0,
            })
            ids.append(ess_id)
        return ids

    # =========================================================================
    # Helpers privados
    # =========================================================================

    @staticmethod
    def _extract_preference_topic(record: Dict[str, Any]) -> str:
        """Extrae el tópico de una preferencia.

        Convención: tag adicional `pref:<topic>` (ej. 'pref:café fuerte').
        Si no existe, intenta heurística sobre el raw_text.
        """
        for t in _tags_of(record):
            if t.startswith("pref:"):
                v = t.split(":", 1)[1].strip()
                if v:
                    return v
        # Fallback heurístico
        text = (record.get("raw_text") or "").lower()
        m = re.search(r"me\s+gusta\s+(?:el\s+|la\s+|los\s+|las\s+)?([\w\s]{3,40})", text)
        if m:
            return m.group(1).strip(" .,;:")
        m = re.search(r"prefiero\s+(?:el\s+|la\s+|los\s+|las\s+)?([\w\s]{3,40})", text)
        if m:
            return m.group(1).strip(" .,;:")
        return ""

    @staticmethod
    def _extract_pattern_topic(record: Dict[str, Any]) -> str:
        """Extrae el verbo/tema central de un pattern via tag 'pat:<topic>'."""
        for t in _tags_of(record):
            if t.startswith("pat:"):
                v = t.split(":", 1)[1].strip()
                if v:
                    return v
        return ""

    @staticmethod
    def _principle_phrase(line: str) -> str:
        """Normaliza el principio a una frase utilizable.

        Si el line ya parece principio ('prefiero ...', 'evito ...',
        'siempre ...'), lo devuelve tal cual con la persona inyectada.
        """
        clean = line.strip()
        if not clean:
            return ""
        # Si arranca en primera persona ('prefiero', 'evito', etc.),
        # la convertimos a tercera persona del user.
        m = re.match(r"^(prefiero|evito|siempre|nunca|me\s+gusta)\b", clean, re.IGNORECASE)
        if m:
            return clean[0].upper() + clean[1:]
        return clean
