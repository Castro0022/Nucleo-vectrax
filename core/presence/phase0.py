"""
core/presence/phase0.py — Fase 0 de la Presencia
=================================================
Pregunta de punta a punta, ANTES de construir nada visual (spec §9 Fase 0):

    "¿Qué estás haciendo ahora?"
        → respuesta generada desde datos persistidos (op_cycles)
        → conjunto de procedencia: los id EXACTOS de los ciclos que la produjeron
        → expand(procedencia) devuelve EXACTAMENTE esos registros. Ni uno más.

Principios que codifica (spec §5, §6, §9):
  - Regla de evidencia (§5): nunca inventa. Solo proyecta lo que leyó de
    `op_cycles`. Si no hay datos, lo dice; no fabrica una cifra.
  - Determinismo (§9): la respuesta es una PROYECCIÓN determinista de los
    datos, NO una decisión de un LLM. Mismos datos + misma pregunta ⇒ misma
    respuesta y mismo conjunto de procedencia. No se usa ningún LLM aquí.
  - Expansión reversible (§6): la respuesta CARGA su procedencia; expandir es
    una proyección determinista de esa procedencia, no un juicio del modelo.
    Si una respuesta no tiene procedencia, no se puede expandir (y se ve).

Sin presencia, sin animación, sin estilo. Texto plano.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# La pregunta canónica de la Fase 0. Fija: la Fase 0 prueba UN camino.
QUESTION = "¿Qué estás haciendo ahora?"

# Cuántos ciclos recientes definen "ahora". La ventana es parte del contrato
# determinista: misma ventana + mismos datos ⇒ misma respuesta.
RECENT_LIMIT = 5

# Columnas de op_cycles que la proyección necesita. Explícitas (no SELECT *)
# para que el contrato sea estable aunque el esquema crezca.
_COLS = (
    "id",
    "timestamp",
    "channel",
    "perceive_intent",
    "decide_route",
    "act_latency_ms",
    "verify_passed",
)


def _vault_dir() -> str:
    """Directorio de estado volátil (honra VECTRAX_VAULT_DIR, igual que el resto)."""
    return os.environ.get(
        "VECTRAX_VAULT_DIR",
        os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
    )


def _db_path(db_path: Optional[str] = None) -> str:
    return db_path or os.path.join(_vault_dir(), "operational_cycles.db")


@dataclass(frozen=True)
class PresenceAnswer:
    """Respuesta de la Fase 0 con su procedencia inmutable.

    `provenance` es la tupla ORDENADA de id de op_cycles que produjeron la
    respuesta. `expand(answer.provenance)` devuelve exactamente esos registros.
    """

    question: str
    text: str
    provenance: Tuple[str, ...]

    @property
    def has_provenance(self) -> bool:
        return len(self.provenance) > 0

    @property
    def provenance_hash(self) -> str:
        """Firma determinista del conjunto de procedencia (para comparar)."""
        joined = "|".join(self.provenance)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "text": self.text,
            "provenance": list(self.provenance),
            "provenance_count": len(self.provenance),
            "provenance_hash": self.provenance_hash,
        }


def _connect(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _read_recent_cycles(limit: int, db_path: str) -> List[Dict[str, Any]]:
    """Lee los `limit` ciclos más recientes, en orden DETERMINISTA.

    Orden: timestamp DESC, id DESC. Con los mismos datos siempre devuelve la
    misma secuencia (el id desempata timestamps idénticos).
    """
    conn = _connect(db_path)
    if conn is None:
        return []
    try:
        cols = ", ".join(_COLS)
        rows = conn.execute(
            f"SELECT {cols} FROM op_cycles "
            f"ORDER BY timestamp DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _compose(rows: List[Dict[str, Any]]) -> str:
    """Proyección determinista → texto plano, primera persona, español (§5).

    Reporta el DELTA (el último ciclo) + una síntesis mínima de la ventana.
    Sin LLM: es una plantilla llenada con los datos leídos.
    """
    last = rows[0]
    intent = last["perceive_intent"] or "—"
    route = last["decide_route"] or "—"
    channel = last["channel"] or "—"
    latency = float(last["act_latency_ms"] or 0.0)
    verify = "ok" if int(last["verify_passed"] or 0) else "no"

    # Distribución determinista de rutas en la ventana (orden estable).
    routes: Dict[str, int] = {}
    for r in rows:
        k = r["decide_route"] or "—"
        routes[k] = routes.get(k, 0) + 1
    # Orden: por conteo DESC, luego nombre ASC → estable.
    dist = ", ".join(
        f"{k}×{v}" for k, v in sorted(routes.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    return (
        f"Ahora: {intent} por {route} en {channel} "
        f"({latency:.0f}ms, verify={verify}). "
        f"Ventana: {len(rows)} ciclos [{dist}]."
    )


def answer_now(limit: int = RECENT_LIMIT, db_path: Optional[str] = None) -> PresenceAnswer:
    """Responde la pregunta canónica desde `op_cycles`, con procedencia.

    Determinista y sin LLM. Si no hay datos, lo dice (regla de evidencia §5)
    y devuelve procedencia vacía (una respuesta sin procedencia no se puede
    expandir, y eso se ve).
    """
    path = _db_path(db_path)
    rows = _read_recent_cycles(limit, path)

    if not rows:
        return PresenceAnswer(
            question=QUESTION,
            text="No tengo ciclos registrados que mirar. No lo he mirado más allá de op_cycles.",
            provenance=(),
        )

    text = _compose(rows)
    provenance = tuple(str(r["id"]) for r in rows)
    return PresenceAnswer(question=QUESTION, text=text, provenance=provenance)


def expand(provenance: Tuple[str, ...], db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Devuelve EXACTAMENTE los registros de la procedencia dada (§6).

    - Proyección determinista de la procedencia, no un juicio del modelo.
    - Preserva el orden de `provenance` y NO añade nada fuera de ese conjunto.
    - Los id ausentes en la BD simplemente no aparecen (no se inventan).
    """
    ids = list(provenance)
    if not ids:
        return []
    path = _db_path(db_path)
    conn = _connect(path)
    if conn is None:
        return []
    try:
        cols = ", ".join(_COLS)
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT {cols} FROM op_cycles WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {str(r["id"]): dict(r) for r in rows}
        # Reproyecta en el orden EXACTO de la procedencia; nada extra.
        return [by_id[i] for i in ids if i in by_id]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
