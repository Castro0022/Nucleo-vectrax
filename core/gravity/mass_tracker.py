"""
core/gravity/mass_tracker.py — Masa cognitiva por categoría.

Cada interacción suma masa al nodo correspondiente en el Universe:
    Vision   → +5    foto, imagen, OCR, recurso visual
    Persona  → +8    mención de un contacto registrado (face_memory)
    Emoción  → +10   carga emocional fuerte (crisis, alegría intensa,
                     soporte) — la categoría con mayor peso porque
                     gravita más fuerte en la memoria del user
    Salud    → +10   temas médicos, diagnósticos, síntomas, tratamientos
                     — mismo peso que EMOCION; marcado como sensible para
                     que el filtro de nudges lo excluya por tag explícito

API pública:
    MassKind                  Enum (VISION / PERSONA / EMOCION / SALUD)
    MASS_VALUES               mapping declarativo
    add_mass(node_id, kind, store=None) -> float (nuevo total)
    get_mass(node_id, store=None) -> float
    MassTracker(store, universe=None)

`store` es un SQLiteVectorStore (persiste mass en deep_memory.mass).
`universe` es opcional — si está disponible, el tracker invoca
`universe.update_node_mass(node_id, total)` tras cada add_mass para
mantener la representación gravitacional sincronizada. No falla si el
Universe no existe o no tiene el método; cae a best-effort.

El diseño separa el COMPUTO (cuánta masa se suma) de la PERSISTENCIA
(SQLite + Universe). Eso permite mock-ear cualquiera de los dos en
tests sin tocar el otro.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger("vectrax.gravity.mass")


# ===========================================================================
# Categorías y valores
# ===========================================================================

class MassKind(str, Enum):
    """Tipo de evento que suma masa al nodo."""
    VISION  = "vision"
    PERSONA = "persona"
    EMOCION = "emocion"
    SALUD   = "salud"   # temas médicos / salud — excluido de nudge context


# Categorías sensibles: nunca se usan en contexto de nudges de presencia.
# Consultado por continuity_reentry._is_sensitive_topic vía tag match.
SENSITIVE_KINDS: frozenset[MassKind] = frozenset({
    MassKind.EMOCION,
    MassKind.SALUD,
})


# Valores declarativos. Modificarlos aquí ajusta toda la balanza
# gravitacional del sistema.
MASS_VALUES = {
    MassKind.VISION:  5.0,
    MassKind.PERSONA: 8.0,
    MassKind.EMOCION: 10.0,
    MassKind.SALUD:   10.0,  # mismo peso que EMOCION
}


def mass_for(kind) -> float:
    """Devuelve el valor numérico asociado al MassKind. Acepta str o Enum."""
    if isinstance(kind, MassKind):
        return MASS_VALUES[kind]
    if isinstance(kind, str):
        try:
            return MASS_VALUES[MassKind(kind.lower())]
        except (KeyError, ValueError):
            return 0.0
    return 0.0


# ===========================================================================
# Tracker
# ===========================================================================

class MassTracker:
    """Coordina escritura de masa en el store + sync con Universe.

    `store` debe exponer `add_mass(node_id, delta) -> float` (nuevo total)
    como SQLiteVectorStore. `universe` es opcional; si no existe, los
    sync calls se ignoran silenciosamente.
    """

    def __init__(self, store, universe=None) -> None:
        self.store = store
        self.universe = universe

    def add(self, node_id: str, kind) -> float:
        """Suma la masa correspondiente al `kind` y sincroniza el Universe.

        Returns:
            Nuevo total de masa para el nodo. 0.0 si node_id es inválido.
        """
        if not node_id:
            return 0.0
        delta = mass_for(kind)
        if delta <= 0:
            logger.debug("mass_tracker: unknown kind %r, no-op", kind)
            return self.get(node_id)
        new_total = float(self.store.add_mass(node_id, delta))
        self._sync_universe(node_id, new_total)
        logger.info(
            "mass: %s += %.1f (kind=%s) → total=%.1f",
            node_id, delta,
            kind.value if isinstance(kind, MassKind) else str(kind),
            new_total,
        )
        return new_total

    def get(self, node_id: str) -> float:
        """Lee la masa actual del nodo. 0.0 si no existe."""
        if not node_id:
            return 0.0
        try:
            cur = self.store.conn.execute(
                "SELECT mass FROM deep_memory WHERE id = ?", (node_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    # -- internal -----------------------------------------------------------

    def _sync_universe(self, node_id: str, total: float) -> None:
        """Best-effort sync con el Universe. Nunca raise."""
        if self.universe is None:
            return
        try:
            update = getattr(self.universe, "update_node_mass", None)
            if callable(update):
                update(node_id, float(total))
                return
            # API alternativa: Universe.set_mass(node_id, mass)
            set_mass = getattr(self.universe, "set_mass", None)
            if callable(set_mass):
                set_mass(node_id, float(total))
        except Exception as exc:
            logger.debug("universe sync swallowed: %s", exc)


# ===========================================================================
# Funciones de conveniencia (top-level)
# ===========================================================================

def add_mass(node_id: str, kind, store, universe=None) -> float:
    """Helper de top-level: equivalente a MassTracker(store, universe).add."""
    return MassTracker(store, universe).add(node_id, kind)


def get_mass(node_id: str, store) -> float:
    """Helper de top-level: lee la masa del nodo."""
    return MassTracker(store).get(node_id)
