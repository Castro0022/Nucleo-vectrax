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
    -- PARTE 6 (2026-09-20): default corregido de 1 -> 0. "verify_passed"
    -- NUNCA debe leerse como verdadero por defecto cuando el auditor no
    -- corrió (verify_ran=0) -- ese era exactamente el defecto silencioso
    -- que permitía a un consumidor ingenuo (p.ej. "WHERE verify_passed=1")
    -- contar ciclos NO verificados como si hubieran pasado una verificación
    -- real. `CycleObserver.set_verify()` refuerza esto en código: si
    -- `ran=False`, `passed` se fuerza a `False` sin importar lo que el
    -- caller haya pasado.
    verify_passed       INTEGER NOT NULL DEFAULT 0,
    verify_rewritten    INTEGER NOT NULL DEFAULT 0,

    -- RESPONDER
    respond_len         INTEGER NOT NULL DEFAULT 0,
    respond_source      TEXT NOT NULL DEFAULT '',

    -- VERIFICACION FINAL (corrección 2026-09-20: "OK" antes conflaba tres
    -- conceptos distintos -- ejecución terminada, mensaje entregado, y
    -- respuesta verificada contra evidencia. Ahora son columnas separadas:
    --   delivered: el mensaje llegó al canal externo (p.ej. Telegram)
    --   grounded:  la respuesta está respaldada por evidencia real
    --              (memoria propia con contenido, o fuentes externas reales)
    --   verified:  cada afirmación está respaldada por evidencia
    --              perteneciente al MISMO usuario (nunca fuentes externas)
    -- "success"/"completed" (abajo) solo significa que el ciclo terminó sin
    -- excepción y produjo una respuesta no vacía -- NO que sea correcta.
    delivered           INTEGER NOT NULL DEFAULT 0,
    grounded            INTEGER NOT NULL DEFAULT 0,
    verified            INTEGER NOT NULL DEFAULT 0,

    -- PARTE 6 (2026-09-20) -- CONTRATO HONESTO, 3 columnas más, ninguna
    -- colapsada con las de arriba:
    --   memory_consulted: la memoria propia del usuario fue efectivamente
    --                     consultada durante este ciclo (independiente de
    --                     si aportó evidencia usable).
    --   evidence_found:   se localizó evidencia REAL (propia o externa),
    --                     independiente de si esa evidencia fue suficiente
    --                     para fundamentar la respuesta final (`grounded`)
    --                     -- una consulta puede encontrar evidencia parcial
    --                     o irrelevante y aun así terminar en abstención.
    --   abstained:        el sistema declaró explícitamente que no tenía
    --                     evidencia suficiente en vez de fabricar una
    --                     respuesta (CLARIFICATION, o el "no lo sé" honesto
    --                     de un ejecutor específico). `success` puede ser 1
    --                     con `abstained`=1 -- abstenerse con honestidad es
    --                     un ciclo completado correctamente, no un fallo.
    memory_consulted    INTEGER NOT NULL DEFAULT 0,
    evidence_found      INTEGER NOT NULL DEFAULT 0,
    abstained           INTEGER NOT NULL DEFAULT 0,

    -- META
    total_latency_ms    REAL NOT NULL DEFAULT 0.0,
    success             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_op_ts       ON op_cycles(timestamp);
CREATE INDEX IF NOT EXISTS idx_op_route    ON op_cycles(decide_route);
CREATE INDEX IF NOT EXISTS idx_op_intent   ON op_cycles(perceive_intent);
CREATE INDEX IF NOT EXISTS idx_op_success  ON op_cycles(success);
"""

# Migración aditiva: bases de datos ya existentes (creadas antes de esta
# corrección) no tienen las 3 columnas nuevas -- CREATE TABLE IF NOT EXISTS
# no las agrega a una tabla ya existente. ALTER TABLE ... ADD COLUMN falla
# silenciosamente (columna ya existe) en bases nuevas.
#
# IMPORTANTE: el ÍNDICE sobre `verified` NO puede vivir dentro de `_CREATE`
# -- `executescript(_CREATE)` corre TODAS sus sentencias en una base ya
# existente (creada antes de esta corrección) ANTES de que estas migraciones
# ALTER TABLE se ejecuten, y `CREATE INDEX ... ON op_cycles(verified)`
# fallaría con "no such column: verified" -- exactamente el bug real
# detectado al probar en producción (2026-09-20): el commit fallaba
# silenciosamente y Pipeline Train dejó de recibir CUALQUIER ciclo nuevo.
# El índice se crea aquí, DESPUÉS de que las columnas ya existen.
_MIGRATE_COLUMNS = (
    "ALTER TABLE op_cycles ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE op_cycles ADD COLUMN grounded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE op_cycles ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_op_verified ON op_cycles(verified)",
    # PARTE 6 (2026-09-20): mismo patrón aditivo -- bases existentes no
    # tienen estas 3 columnas hasta que corre este ALTER TABLE.
    "ALTER TABLE op_cycles ADD COLUMN memory_consulted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE op_cycles ADD COLUMN evidence_found INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE op_cycles ADD COLUMN abstained INTEGER NOT NULL DEFAULT 0",
)

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
    # PARTE 6 (2026-09-20): default corregido de True -> False. Un ciclo que
    # nunca llama a `set_verify()` (o lo llama con `ran=False`) NUNCA debe
    # reportar `verify_passed=True` -- eso sería afirmar una verificación
    # que jamás ocurrió. Ver `set_verify()` para el enforcement en código.
    verify_passed: bool = False     # ¿la respuesta pasó la auditoría? (solo significativo si verify_ran=True)
    verify_rewritten: bool = False  # ¿la respuesta fue reescrita?

    # RESPONDER — qué se envió
    respond_len: int = 0            # longitud de la respuesta enviada
    respond_source: str = ""        # fuente final (memory, llm, online, self_aware, etc.)

    # VERIFICACION FINAL — 3 conceptos SEPARADOS, nunca colapsados en "OK"
    delivered: bool = False         # ¿el mensaje llegó al canal externo (Telegram)?
    grounded: bool = False          # ¿respaldada por evidencia real (propia o externa)?
    verified: bool = False          # ¿cada afirmación respaldada por evidencia del MISMO usuario?

    # PARTE 6 (2026-09-20) — contrato honesto, ver docstring de columnas en `_CREATE`
    memory_consulted: bool = False  # ¿se consultó la memoria propia del usuario?
    evidence_found: bool = False    # ¿se localizó evidencia real (propia o externa)?
    abstained: bool = False         # ¿el sistema declaró honestamente que no sabía?

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
    for stmt in _MIGRATE_COLUMNS:
        try:
            conn.execute(stmt)
        except Exception:
            pass  # columna ya existe (DB creada por primera vez con _CREATE)
    return conn


def _commit_cycle(cycle: OperationalCycle) -> None:
    """Persiste el ciclo completo en el ledger operativo."""
    try:
        conn = _conn()
        # PARTE 6: enforcement final e incondicional -- sin importar qué
        # valor tenga `cycle.verify_passed` en este punto, si el auditor no
        # corrió (`verify_ran=False`) se persiste `verify_passed=False`.
        # Doble refuerzo intencional junto con `CycleObserver.set_verify()`:
        # este es el ÚLTIMO punto antes de escribir en disco, así que ninguna
        # ruta de construcción futura de `OperationalCycle` puede saltarse
        # esta garantía.
        _verify_passed_honest = bool(cycle.verify_passed) and bool(cycle.verify_ran)
        conn.execute(
            """INSERT OR REPLACE INTO op_cycles (
                id, timestamp, channel, user_tier,
                perceive_intent, perceive_lang, perceive_words,
                interpret_action, interpret_reason,
                decide_route, decide_strategy, decide_confidence,
                act_latency_ms, act_empty, act_fallback,
                verify_ran, verify_passed, verify_rewritten,
                respond_len, respond_source,
                delivered, grounded, verified,
                memory_consulted, evidence_found, abstained,
                total_latency_ms, success
            ) VALUES (?,?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?, ?,?,?, ?,?, ?,?,?, ?,?,?, ?,?)""",
            (
                cycle.id, cycle.timestamp, cycle.channel, cycle.user_tier,
                cycle.perceive_intent, cycle.perceive_lang, cycle.perceive_words,
                cycle.interpret_action, cycle.interpret_reason,
                cycle.decide_route, cycle.decide_strategy, round(cycle.decide_confidence, 4),
                round(cycle.act_latency_ms, 2), int(cycle.act_empty), int(cycle.act_fallback),
                int(cycle.verify_ran), int(_verify_passed_honest), int(cycle.verify_rewritten),
                cycle.respond_len, cycle.respond_source,
                int(cycle.delivered), int(cycle.grounded), int(cycle.verified),
                int(cycle.memory_consulted), int(cycle.evidence_found), int(cycle.abstained),
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

    def __init__(
        self,
        channel: str = "telegram",
        user_tier: str = "free",
        cycle_id: str = "",
        start_time: float = 0.0,
    ) -> None:
        # cycle_id (auditoría 2026-09-11/13): si el caller ya tiene un
        # correlation_id para esta request (external_gateway.py), lo
        # reutiliza como id de este ciclo en vez de generar uno nuevo
        # desconectado — permite unir op_cycles.db con el resto de la
        # telemetría (ledger, router_activation.jsonl) por el mismo ID.
        # Si se omite, se genera uno nuevo (comportamiento previo).
        #
        # start_time (corrección 2026-09-20, Pipeline Train): permite
        # construir el observador DESPUÉS de que el ciclo real ya terminó
        # (p.ej. en el punto de salida común de pipeline_worker.py, cuando
        # NucleusAuthority ya resolvió sin pasar por ExternalGateway) sin
        # que `total_latency_ms()` colapse a ~0ms -- `_start` por defecto
        # es `time.time()` en el momento de CONSTRUCCIÓN del objeto, que en
        # ese caso sería muy posterior al inicio real de la request.
        if cycle_id:
            self._cycle = OperationalCycle(
                id=cycle_id, channel=channel, user_tier=user_tier,
            )
        else:
            self._cycle = OperationalCycle(channel=channel, user_tier=user_tier)
        if start_time > 0:
            self._cycle._start = start_time
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
        """Registra si el auditor de respuesta corrió y, si corrió, si pasó.

        PARTE 6 (2026-09-20): `passed` NUNCA se persiste como `True` cuando
        `ran=False` -- sin importar qué valor pase el caller. "El auditor no
        corrió" y "el auditor corrió y aprobó" son hechos distintos; el
        segundo nunca puede inferirse honestamente de la ausencia del
        primero. Callers existentes que llaman `set_verify(ran=False,
        passed=True, ...)` (comportamiento previo, ver `pipeline_worker.py`)
        siguen funcionando sin cambios en su firma -- el valor efectivo
        simplemente se corrige aquí.
        """
        self._cycle.verify_ran = ran
        self._cycle.verify_passed = bool(passed) if ran else False
        self._cycle.verify_rewritten = rewritten

    def set_respond(self, length: int = 0, source: str = "") -> None:
        self._cycle.respond_len = length
        self._cycle.respond_source = source[:60]

    def set_memory(
        self,
        consulted: bool = False,
        evidence_found: bool = False,
    ) -> None:
        """Registra si se consultó memoria propia y si se localizó evidencia
        real -- independientes entre sí y de `grounded`/`verified` (ver
        docstring de columnas en `_CREATE`). `evidence_found=True` con
        `grounded=False` es un caso válido y esperado: se encontró algo,
        pero no era suficiente/relevante para fundamentar la respuesta.
        """
        self._cycle.memory_consulted = consulted
        self._cycle.evidence_found = evidence_found

    def set_verification(
        self,
        delivered: bool = False,
        grounded: bool = False,
        verified: bool = False,
        abstained: bool = False,
    ) -> None:
        """Registra los conceptos de verificación final SEPARADOS -- nunca
        colapsados en un solo "OK". Ver docstring de columnas en `_CREATE`.

        - `delivered`: el mensaje efectivamente llegó al canal externo.
        - `grounded`: la respuesta está respaldada por evidencia real
          (memoria propia con contenido real, o fuentes externas reales) --
          nunca simplemente "el ciclo terminó sin error".
        - `verified`: cada afirmación está respaldada por evidencia
          perteneciente al MISMO usuario que preguntó -- más estricto que
          `grounded` (una respuesta ONLINE puede estar grounded en fuentes
          reales sin estar `verified` en el sentido de "evidencia propia
          del usuario").
        - `abstained`: el sistema declaró explícitamente que no tenía
          evidencia suficiente (CLARIFICATION u otro "no lo sé" honesto) en
          vez de fabricar una respuesta. Independiente de `success` --
          abstenerse con honestidad es un ciclo completado, no un fallo.
        """
        self._cycle.delivered = delivered
        self._cycle.grounded = grounded
        self._cycle.verified = verified
        self._cycle.abstained = abstained

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
