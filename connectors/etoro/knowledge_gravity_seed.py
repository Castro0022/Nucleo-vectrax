"""
connectors/etoro/knowledge_gravity_seed.py — Siembra de conocimiento (masa cero).

"La escuela crea el conocimiento; la experiencia posterior lo enriquece."

Crea, UNA vez, una estrella gravitacional por cada concepto de TA-Lib que
`connectors.market.ta_knowledge` sabe calcular — no por cada símbolo, no por
cada timeframe, no por cada salida numérica de una función con múltiples
salidas (MACD, BBANDS, AROON, ... siguen siendo UN concepto cada una).

Convención de identidad:
    fingerprint = f"market_knowledge:{nombre_funcion}"
    domain      = "market"
    intent       = "ta_indicator"
    hits         = 0       — existe, no se ha activado todavía
    cc_score     = 0.0
    outcome_history / activation_history = []  — sin experiencia todavía

Mecanismo: el MISMO `GravityIndex` que ya existe. `record_event()` no sirve
para esto (siempre crea con hits=1 -- implica que algo ocurrió); se usa
`GravityIndex.update_records()`, el método de escritura cruda que
`core/learn/constellation.py` ya usa para persistir cambios de estado que no
son un evento. Cuando Market reconozca después alguno de estos conceptos en
una observación real, `record_event(fingerprint=ese_mismo_fingerprint, ...)`
encontrará el registro YA EXISTENTE y lo activará (hits += 1) — esa conexión
es una etapa futura, deliberadamente NO implementada aquí.

CRÍTICO: `update_records()` hace `_write_to_disk(records)` con EXACTAMENTE
el dict que se le pasa — sustituye el índice completo, no lo fusiona. Por
eso esta siembra SIEMPRE parte de `load_raw()` (el estado real actual) y le
añade las que falten, nunca escribe un dict que contenga solo los 196
nuevos — perder una sola estrella existente por esto sería inaceptable.

Idempotente: una identidad ya presente (de una siembra anterior, o de
cualquier otro origen) NUNCA se sobrescribe ni se cuenta dos veces.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("vectrax.etoro.knowledge_gravity_seed")

FINGERPRINT_PREFIX = "market_knowledge:"
SEED_DOMAIN = "market"
SEED_INTENT = "ta_indicator"


def knowledge_fingerprint(function_name: str) -> str:
    """Identidad gravitacional de un concepto de conocimiento técnico."""
    return f"{FINGERPRINT_PREFIX}{function_name}"


def seed_knowledge_stars() -> Dict[str, Any]:
    """Crea (si no existen ya) una estrella de masa cero por cada función
    del catálogo de `ta_knowledge`. Nunca reactiva ni modifica una estrella
    que ya existía — ni la sembrada antes, ni ninguna de otro origen.

    Returns: {"created": [...], "already_present": [...], "total_before",
              "total_after"} — nunca lanza.
    """
    from core.learn.gravity_engine import get_gravity_index, GravityRecord, Tier, _now_iso
    from connectors.market.ta_knowledge import known_function_names

    gi = get_gravity_index()
    records = gi.load_raw()  # estado REAL completo — nunca se parte de {}
    total_before = len(records)

    now = _now_iso()
    created = []
    already_present = []

    for name in known_function_names():
        fp = knowledge_fingerprint(name)
        if fp in records:
            already_present.append(fp)
            continue
        records[fp] = GravityRecord(
            fingerprint=fp,
            tier=Tier.HOT.value,
            hits=0,
            first_seen=now,
            last_seen="",
            cc_score=0.0,
            impact="low",
            domain=SEED_DOMAIN,
            intent=SEED_INTENT,
            outcome_history=[],
            verified_outcomes=[],
            activation_history=[],
            decay_factor=1.0,
            summary=f"Conocimiento técnico formal: {name} (TA-Lib) — sin activar todavía",
        )
        created.append(fp)

    if created:
        gi.update_records(records)  # UNA sola escritura, con TODO el índice

    total_after = total_before + len(created)
    logger.info(
        "[KNOWLEDGE_SEED] creadas=%d ya_presentes=%d total_antes=%d total_despues=%d",
        len(created), len(already_present), total_before, total_after,
    )
    return {
        "created": created,
        "already_present": already_present,
        "total_before": total_before,
        "total_after": total_after,
    }
