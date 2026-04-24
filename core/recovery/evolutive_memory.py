"""
Vectrax — segunda memoria del organismo
=========================================
La memoria CONVERSACIONAL recuerda lo que se dijo.
La memoria EVOLUTIVA recuerda lo que dolió técnicamente y qué órgano
nació para impedir su retorno.

Sin la segunda, Vectrax sería software. Con la segunda, es organismo.

API:
    has_motor_for(signature)       → bool
    route_to_existing_motor(sig)   → str (motor_id)
    bind_motor(signature, motor)   → None
    get_related(anomaly)           → lista de heridas pasadas parecidas
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from core.recovery.anomaly import Anomaly
from core.recovery.classifier import ErrorSignature
from core.recovery.motor_factory import CandidateMotor

logger = logging.getLogger("vectrax.recovery.evolutive_memory")

_DB_PATH = os.environ.get(
    "VECTRAX_EVOLUTIVE_MEMORY",
    "/var/log/vectrax/evolutive_memory.db",
)


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=5.0)
    c.execute("""
        CREATE TABLE IF NOT EXISTS organs (
            species     TEXT NOT NULL,
            mechanism   TEXT NOT NULL,
            motor_id    TEXT NOT NULL,
            installed_at REAL NOT NULL,
            PRIMARY KEY (species, mechanism)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS wounds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            anomaly_sig  TEXT NOT NULL,
            species      TEXT NOT NULL,
            mechanism    TEXT NOT NULL,
            ts           REAL NOT NULL,
            evidence     TEXT
        )
    """)
    return c


def has_motor_for(signature: ErrorSignature) -> bool:
    try:
        c = _conn()
        row = c.execute(
            "SELECT 1 FROM organs WHERE species=? AND mechanism=?",
            (signature.species, signature.mechanism),
        ).fetchone()
        c.close()
        return row is not None
    except Exception as exc:
        logger.debug("has_motor_for failed: %s", exc)
        return False


def route_to_existing_motor(signature: ErrorSignature) -> Optional[str]:
    try:
        c = _conn()
        row = c.execute(
            "SELECT motor_id FROM organs WHERE species=? AND mechanism=?",
            (signature.species, signature.mechanism),
        ).fetchone()
        c.close()
        return row[0] if row else None
    except Exception as exc:
        logger.debug("route_to_existing_motor failed: %s", exc)
        return None


def bind_motor(
    signature: ErrorSignature, candidate: CandidateMotor,
) -> None:
    motor_id = f"{signature.species}_{signature.mechanism}"
    try:
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO organs "
            "(species, mechanism, motor_id, installed_at) VALUES (?,?,?,?)",
            (signature.species, signature.mechanism, motor_id, time.time()),
        )
        c.commit()
        c.close()
        logger.info("evolutive_memory: órgano ligado — %s", motor_id)
    except Exception as exc:
        logger.debug("bind_motor failed: %s", exc)


def record_wound(
    anomaly: Anomaly, signature: ErrorSignature,
) -> None:
    try:
        c = _conn()
        c.execute(
            "INSERT INTO wounds "
            "(anomaly_sig, species, mechanism, ts, evidence) VALUES (?,?,?,?,?)",
            (anomaly.signature, signature.species, signature.mechanism,
             anomaly.ts, str(anomaly.evidence)[:2000]),
        )
        c.commit()
        c.close()
    except Exception as exc:
        logger.debug("record_wound failed: %s", exc)


def get_related(anomaly: Anomaly) -> List[Dict[str, Any]]:
    """Heridas pasadas con signature parecido — contexto para el forge."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT species, mechanism, ts FROM wounds "
            "WHERE anomaly_sig LIKE ? ORDER BY ts DESC LIMIT 20",
            (f"%{anomaly.signature[:6]}%",),
        ).fetchall()
        c.close()
        return [
            {"species": r[0], "mechanism": r[1], "ts": r[2]}
            for r in rows
        ]
    except Exception as exc:
        logger.debug("get_related failed: %s", exc)
        return []
