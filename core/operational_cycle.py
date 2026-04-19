"""
Vectrax Operational Cycle — Aprendizaje Operativo
===================================================
Cada mensaje que entra obedece el mismo principio:

  percibir → interpretar → decidir → actuar → verificar → responder → registrar

Este módulo implementa ese ciclo como una capa envolvente del gateway.
No reemplaza el pipeline actual — lo gobierna y lo observa.

Cada paso deja una huella abstracta en el ledger operativo.
La suma de esas huellas es la memoria operativa de Vectrax.
De esa memoria nacen patrones. De los patrones, reglas de mejora.

Principio de privacidad:
  NUNCA se almacena texto literal del usuario ni de la respuesta.
  Solo metadata abstracta: intents, rutas, latencias, resultados booleanos.

Creado: 2026-04-01
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operational_cycle")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "operational_cycles.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS op_cycles (
    id                  TEXT PRIMARY KEY,
    timestamp           REAL NOT NULL,
    channel             TEXT NOT NULL DEFAULT '',
    user_tier           TEXT NOT NULL DEFAULT 'free',

    -- PERCIBIR
    perceive_intent     TEXT NOT NULL DEFAULT '',
    perceive_lang       TEXT NOT NULL DEFAULT '',
    perceive_words      INTEGER NOT NULL DEFAULT 0,

    -- INTERPRETAR
    interpret_action    TEXT NOT NULL DEFAULT '',
    interpret_reason    TEXT NOT NULL DEFAULT '',

    -- DECIDIR
    decide_route        TEXT NOT NULL DEFAULT '',
    decide_strategy     TEXT NOT NULL DEFAULT '',
    decide_confidence   REAL NOT NULL DEFAULT 0.0,

    -- ACTUAR
    act_latency_ms      REAL NOT NULL DEFAULT 0.0,
    act_empty           INTEGER NOT NULL DEFAULT 0,
    act_fallback        INTEGER NOT NULL DEFAULT 0,

    -- VERIFICAR
    verify_ran          INTEGER NOT NULL DEFAULT 0,
    verify_passed       INTEGER NOT NULL DEFAULT 1,
    verify_rewritten    INTEGER NOT NULL DEFAULT 0,

    -- RESPONDER
    respond_len         INTEGER NOT NULL DEFAULT 0,
    respond_source      TEXT NOT NULL DEFAULT '',

    -- META
    total_latency_ms    REAL NOT NULL DEFAULT 0.0,
    success             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_op_ts       ON op_cycles(timestamp);
CREATE INDEX IF NOT EXISTS idx_op_route    ON op_cycles(decide_route);
CREATE INDEX IF NOT EXISTS idx_op_intent   ON op_cycles(perceive_intent);
CREATE INDEX IF NOT EXISTS idx_op_success  ON op_cycles(success);
"""

MAX_RECORDS = 10000  # rotación automática


# ---------------------------------------------------------------------------
# Data model — el ciclo completo de un mensaje
# ---------------------------------------------------------------------------

@dataclass
class OperationalCycle:
    """
    Registro de un ciclo operativo completo.
    Cada campo corresponde a un paso del principio.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = field(default_factory=time.time)
    channel: str = ""
    user_tier: str = "free"

    # PERCIBIR — qué entró
    perceive_intent: str = ""       # intent detectado (search_intent, vectrax_self_reference, etc.)
    perceive_lang: str = ""         # idioma detectado
    perceive_words: int = 0         # longitud abstracta (word count)

    # INTERPRETAR — qué significa
    interpret_action: str = ""      # acción del intake filter (ejecutar, guardar, ignorar)
    interpret_reason: str = ""      # razón del intake filter

    # DECIDIR — qué ruta
    decide_route: str = ""          # ruta elegida (memory, online, llm, self_aware, market, places)
    decide_strategy: str = ""       # estrategia del SmartRouter
    decide_confidence: float = 0.0  # confianza del router

    # ACTUAR — qué se hizo
    act_latency_ms: float = 0.0     # tiempo de resolución
    act_empty: bool = False         # ¿la respuesta fue vacía?
    act_fallback: bool = False      # ¿se usó fallback?

    # VERIFICAR — qué resultó
    verify_ran: bool = False        # ¿el auditor corrió?
    verify_passed: bool = True      # ¿la respuesta pasó la auditoría?
    verify_rewritten: bool = False  # ¿la respuesta fue reescrita?

    # RESPONDER — qué se envió
    respond_len: int = 0            # longitud de la respuesta enviada
    respond_source: str = ""        # fuente final (memory, llm, online, self_aware, etc.)

    # META
    _start: float = field(default_factory=time.time, repr=False)

    def total_latency_ms(self) -> float:
        return round((time.time() - self._start) * 1000, 2)

    @property
    def success(self) -> bool:
        return not self.act_empty and self.respond_len > 0


# ---------------------------------------------------------------------------
# Ledger — persistencia append-only
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE)
    return conn


def _commit_cycle(cycle: OperationalCycle) -> None:
    """Persiste el ciclo completo en el ledger operativo."""
    try:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO op_cycles (
                id, timestamp, channel, user_tier,
                perceive_intent, perceive_lang, perceive_words,
                interpret_action, interpret_reason,
                decide_route, decide_strategy, decide_confidence,
                act_latency_ms, act_empty, act_fallback,
                verify_ran, verify_passed, verify_rewritten,
                respond_len, respond_source,
                total_latency_ms, success
            ) VALUES (?,?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?, ?,?,?, ?,?, ?,?)""",
            (
                cycle.id, cycle.timestamp, cycle.channel, cycle.user_tier,
                cycle.perceive_intent, cycle.perceive_lang, cycle.perceive_words,
                cycle.interpret_action, cycle.interpret_reason,
                cycle.decide_route, cycle.decide_strategy, round(cycle.decide_confidence, 4),
                round(cycle.act_latency_ms, 2), int(cycle.act_empty), int(cycle.act_fallback),
                int(cycle.verify_ran), int(cycle.verify_passed), int(cycle.verify_rewritten),
                cycle.respond_len, cycle.respond_source,
                cycle.total_latency_ms(), int(cycle.success),
            ),
        )
        conn.commit()
        _rotate_if_needed(conn)
        conn.close()
    except Exception as exc:
        logger.debug("op_cycle commit failed: %s", exc)


def _rotate_if_needed(conn: sqlite3.Connection) -> None:
    try:
        count = conn.execute("SELECT COUNT(*) FROM op_cycles").fetchone()[0]
        if count > MAX_RECORDS:
            conn.execute(
                "DELETE FROM op_cycles WHERE id IN "
                "(SELECT id FROM op_cycles ORDER BY timestamp ASC LIMIT ?)",
                (MAX_RECORDS // 5,),
            )
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Observer — API de alto nivel para usar en el gateway
# ---------------------------------------------------------------------------

class CycleObserver:
    """
    Observador de ciclo operativo.

    Uso:
        obs = CycleObserver(channel="telegram", user_tier="free")
        obs.set_perceive(intent="search_intent", lang="es", words=5)
        obs.set_interpret(action="ejecutar", reason="vectrax_self_reference")
        obs.set_decide(route="self_aware", strategy="RESOLVE_ONLINE", confidence=0.9)
        obs.set_act(latency_ms=1200, empty=False, fallback=False)
        obs.set_verify(ran=True, passed=True, rewritten=False)
        obs.set_respond(length=340, source="self_aware")
        obs.commit()   # persiste y dispara learning si hay fallos
    """

    def __init__(self, channel: str = "telegram", user_tier: str = "free") -> None:
        self._cycle = OperationalCycle(channel=channel, user_tier=user_tier)
        self._act_start: float = 0.0

    # -- Pasos del ciclo ---------------------------------------------------

    def set_perceive(self, intent: str = "", lang: str = "", words: int = 0) -> None:
        self._cycle.perceive_intent = intent[:60]
        self._cycle.perceive_lang = lang[:10]
        self._cycle.perceive_words = words

    def set_interpret(self, action: str = "", reason: str = "") -> None:
        self._cycle.interpret_action = action[:50]
        self._cycle.interpret_reason = reason[:100]

    def set_decide(
        self,
        route: str = "",
        strategy: str = "",
        confidence: float = 0.0,
    ) -> None:
        self._cycle.decide_route = route[:60]
        self._cycle.decide_strategy = strategy[:60]
        self._cycle.decide_confidence = confidence
        self._act_start = time.time()

    def set_act(
        self,
        latency_ms: float = 0.0,
        empty: bool = False,
        fallback: bool = False,
    ) -> None:
        self._cycle.act_latency_ms = latency_ms
        self._cycle.act_empty = empty
        self._cycle.act_fallback = fallback

    def set_verify(
        self,
        ran: bool = False,
        passed: bool = True,
        rewritten: bool = False,
    ) -> None:
        self._cycle.verify_ran = ran
        self._cycle.verify_passed = passed
        self._cycle.verify_rewritten = rewritten

    def set_respond(self, length: int = 0, source: str = "") -> None:
        self._cycle.respond_len = length
        self._cycle.respond_source = source[:60]

    # -- Commit ------------------------------------------------------------

    def commit(self) -> OperationalCycle:
        """
        Persiste el ciclo y dispara aprendizaje si hay fallos.
        Retorna el ciclo completo.
        """
        _commit_cycle(self._cycle)

        # Disparar learning pipeline si hubo fallo
        if self._cycle.act_empty or self._cycle.verify_rewritten:
            self._feed_learning()

        logger.debug(
            "op_cycle committed | route=%s lang=%s words=%d "
            "latency=%.0fms empty=%s rewritten=%s success=%s",
            self._cycle.decide_route,
            self._cycle.perceive_lang,
            self._cycle.perceive_words,
            self._cycle.act_latency_ms,
            self._cycle.act_empty,
            self._cycle.verify_rewritten,
            self._cycle.success,
        )
        return self._cycle

    def _feed_learning(self) -> None:
        """Alimenta el LearningPipeline cuando el ciclo tuvo fallos."""
        try:
            from core.learning_cycle.anomaly_detector import InputEvent
            from core.learning_cycle.pipeline import get_learning_pipeline
            reason = "empty_response" if self._cycle.act_empty else "response_rewritten"
            get_learning_pipeline().process_event(InputEvent(
                text=f"[op_cycle_fail:{self._cycle.decide_route}]",
                intent=self._cycle.perceive_intent or "unknown",
                source=self._cycle.channel,
                length=self._cycle.perceive_words,
            ))
        except Exception as exc:
            logger.debug("Learning feed failed: %s", exc)


# ---------------------------------------------------------------------------
# Analytics — para /vx cycle
# ---------------------------------------------------------------------------

def get_cycle_stats(days: int = 7) -> Dict[str, Any]:
    """
    Estadísticas del ciclo operativo para los últimos N días.
    """
    cutoff = time.time() - (days * 86400)
    try:
        conn = _conn()

        total = conn.execute(
            "SELECT COUNT(*) FROM op_cycles WHERE timestamp > ?", (cutoff,)
        ).fetchone()[0]

        success = conn.execute(
            "SELECT COUNT(*) FROM op_cycles WHERE timestamp > ? AND success = 1",
            (cutoff,),
        ).fetchone()[0]

        empty = conn.execute(
            "SELECT COUNT(*) FROM op_cycles WHERE timestamp > ? AND act_empty = 1",
            (cutoff,),
        ).fetchone()[0]

        rewritten = conn.execute(
            "SELECT COUNT(*) FROM op_cycles WHERE timestamp > ? AND verify_rewritten = 1",
            (cutoff,),
        ).fetchone()[0]

        avg_latency = conn.execute(
            "SELECT AVG(act_latency_ms) FROM op_cycles "
            "WHERE timestamp > ? AND act_latency_ms > 0",
            (cutoff,),
        ).fetchone()[0] or 0.0

        # Ruta más usada
        top_route = conn.execute(
            "SELECT decide_route, COUNT(*) as c FROM op_cycles "
            "WHERE timestamp > ? GROUP BY decide_route ORDER BY c DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        # Intent más difícil (más fallos)
        hard_intent = conn.execute(
            "SELECT perceive_intent, COUNT(*) as c FROM op_cycles "
            "WHERE timestamp > ? AND success = 0 "
            "GROUP BY perceive_intent ORDER BY c DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        # Latencia por ruta
        routes_latency = conn.execute(
            "SELECT decide_route, AVG(act_latency_ms), COUNT(*) FROM op_cycles "
            "WHERE timestamp > ? AND act_latency_ms > 0 "
            "GROUP BY decide_route ORDER BY 2 DESC",
            (cutoff,),
        ).fetchall()

        conn.close()

        return {
            "total": total,
            "success": success,
            "success_rate": round(success / max(total, 1) * 100, 1),
            "empty": empty,
            "rewritten": rewritten,
            "avg_latency_ms": round(avg_latency, 0),
            "top_route": top_route[0] if top_route else "-",
            "hard_intent": hard_intent[0] if hard_intent else "-",
            "routes_latency": [
                {"route": r[0], "avg_ms": round(r[1], 0), "count": r[2]}
                for r in routes_latency[:6]
            ],
        }
    except Exception as exc:
        logger.debug("get_cycle_stats failed: %s", exc)
        return {
            "total": 0, "success": 0, "success_rate": 0.0,
            "empty": 0, "rewritten": 0, "avg_latency_ms": 0,
            "top_route": "-", "hard_intent": "-", "routes_latency": [],
        }
