"""
Vectrax — Ritual de escritura
================================
Cierra el ciclo de "guardar". Cuando el usuario pide grabar algo en el núcleo,
devuelve un recibo estructurado que confirma:

  - Qué se grabó (resumen, no eco literal).
  - Dónde quedó (capa, colección, DB).
  - Peso gravitacional inicial.
  - Hechos extraídos (si los hay).
  - Identificador reversible (para poder deshacer).

Sin este recibo, "guardar" no es un acto de memoria, es un eco. Esta capa se
inserta DESPUÉS de store_memory / core_memory.absorb y ANTES de enforce_final_answer.

Principio: si Vectrax no puede decir "sí, lo grabé, y aquí está el recibo",
entonces no lo grabó.

Creado: 2026-04-19
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.ritual_escritura")


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class WriteReceipt:
    """Recibo emitido por el ritual de escritura."""
    receipt_id: str                 # hash corto reversible
    user_id: str
    summary: str                    # qué se grabó (<= 140 chars)
    layer: str                      # "interactions" | "core" | "facts" | "argos"
    weight: float                   # peso inicial (0.0..1.0)
    facts_extracted: int = 0
    absorbed_in_core: bool = False
    reversible: bool = True
    timestamp: float = field(default_factory=time.time)
    sources: List[str] = field(default_factory=list)

    def as_user_message(self, lang: str = "es") -> str:
        """Convierte el recibo en un mensaje breve y directo al usuario."""
        lines: List[str]
        if lang == "es":
            lines = [f"✓ Grabado — {self.summary}"]
            lines.append(f"  núcleo={self.layer} · peso={self.weight:.2f}")
            if self.facts_extracted:
                lines.append(f"  hechos={self.facts_extracted}")
            if self.absorbed_in_core:
                lines.append("  absorbido en core_memory")
            lines.append(f"  id={self.receipt_id}  (reversible)")
        else:
            lines = [f"✓ Stored — {self.summary}"]
            lines.append(f"  layer={self.layer} · weight={self.weight:.2f}")
            if self.facts_extracted:
                lines.append(f"  facts={self.facts_extracted}")
            if self.absorbed_in_core:
                lines.append("  absorbed into core_memory")
            lines.append(f"  id={self.receipt_id}  (reversible)")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "user_id": self.user_id,
            "summary": self.summary,
            "layer": self.layer,
            "weight": self.weight,
            "facts_extracted": self.facts_extracted,
            "absorbed_in_core": self.absorbed_in_core,
            "reversible": self.reversible,
            "timestamp": self.timestamp,
            "sources": list(self.sources),
        }


# ---------------------------------------------------------------------------
# Peso inicial
# ---------------------------------------------------------------------------

def _initial_weight(
    user_input: str,
    facts_extracted: int,
    absorbed_in_core: bool,
    is_principle: bool = False,
) -> float:
    """
    Peso inicial de un recuerdo, en [0.0, 1.0].

    Señales que aumentan el peso:
      - marcado explícito como principio / ley / decisión
      - hechos extraídos (nombres, cifras, fechas)
      - absorbido en core_memory
      - longitud intencional (ni una sola palabra, ni un volcado)
    """
    w = 0.30  # base
    if is_principle:
        w += 0.35
    if absorbed_in_core:
        w += 0.15
    w += min(0.15, facts_extracted * 0.05)

    n = len(user_input.split())
    if 5 <= n <= 80:
        w += 0.05

    return max(0.0, min(1.0, w))


def _short_hash(user_id: str, user_input: str, ts: float) -> str:
    raw = f"{user_id}|{user_input}|{ts}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:10]


def _summarize(user_input: str, limit: int = 140) -> str:
    """Resumen literal recortado; respeta palabras."""
    t = " ".join(user_input.split())
    if len(t) <= limit:
        return t
    cut = t[: limit - 1]
    sp = cut.rfind(" ")
    if sp > 40:
        cut = cut[:sp]
    return cut + "…"


# ---------------------------------------------------------------------------
# Detección de principio / ley / decisión permanente
# ---------------------------------------------------------------------------

_PRINCIPLE_MARKERS = (
    "principio", "ley", "regla", "norma", "decisión", "decision",
    "core rule", "principle", "never forget", "no olvides",
    "recuerda siempre", "para siempre",
)


def _is_principle(user_input: str) -> bool:
    low = user_input.lower()
    return any(m in low for m in _PRINCIPLE_MARKERS)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def emit_receipt(
    *,
    user_id: str,
    user_input: str,
    layer: str,
    facts_extracted: int = 0,
    absorbed_in_core: bool = False,
    sources: Optional[List[str]] = None,
) -> WriteReceipt:
    """
    Emite un recibo de escritura. No graba nada — solo empaqueta la confirmación
    estructurada de lo que las capas de memoria ya hicieron.

    Llamar INMEDIATAMENTE después de store_memory() y absorb() en el pipeline.
    """
    ts = time.time()
    is_principle = _is_principle(user_input)
    receipt = WriteReceipt(
        receipt_id=_short_hash(user_id, user_input, ts),
        user_id=user_id,
        summary=_summarize(user_input),
        layer=layer,
        weight=_initial_weight(user_input, facts_extracted, absorbed_in_core, is_principle),
        facts_extracted=facts_extracted,
        absorbed_in_core=absorbed_in_core,
        reversible=True,
        timestamp=ts,
        sources=list(sources or []),
    )
    logger.info(
        "Ritual write | user=%s | layer=%s | weight=%.2f | facts=%d | core=%s | id=%s",
        user_id[:20], layer, receipt.weight, facts_extracted,
        absorbed_in_core, receipt.receipt_id,
    )
    return receipt


def format_user_confirmation(receipt: WriteReceipt, lang: str = "es") -> str:
    """Azúcar: convierte un recibo en mensaje directo al usuario."""
    return receipt.as_user_message(lang=lang)
