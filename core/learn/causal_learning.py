"""
core/learn/causal_learning.py — Puente causal convergencia → aprendizaje → criterio.

QUÉ RESUELVE
------------
La auditoría del 2026-09-22 demostró dos enlaces AUSENTES en el ciclo causal:

  * `convergencia → aprendizaje`: ninguna función leía `convergences` para
    producir un artefacto de aprendizaje.
  * `aprendizaje → criterio`: `core/learn/criterion.py` no mencionaba
    convergencias ni reglas aprendidas (grep con 0 resultados).

Este módulo es la SSOT de ese puente, y es EFECTIVO EN PRODUCCIÓN: un
aprendizaje que supera el gate entra en el criterio real inmediatamente. No
existe modo sombra, ni criterio paralelo, ni variable de entorno para posponer
el efecto, ni una segunda activación futura.

LA SECUENCIA
------------
    observación → patrón cualificado → convergencia canónica → GATE
    → aprendizaje persistido → criterio efectivo → decisión
    → aplicación o abstención → outcome → refuerzo/debilitamiento/contradicción

Se persiste la identidad completa, de modo que si falta un eslabón se puede
decir exactamente CUÁL:

    evidence_ids → source_pattern_ids → convergence_id → learning_id
    → criterion_version → decision_id → application_id → outcome_id

PRODUCCIÓN DIRECTA NO ES PROMOCIÓN AUTOMÁTICA
---------------------------------------------
El incidente de las 121.206 convergencias demuestra que el VOLUMEN y la
REPETICIÓN del escáner no son evidencia epistemológica. Una convergencia solo
produce `LEARNED` si supera las diez condiciones de `evaluate_convergence()`.
Mientras no las supere permanece en `CONVERGED_CANDIDATE` — no porque esté
"en sombra", sino porque todavía NO cumple la evidencia exigida.

LA FALSA REPETICIÓN
-------------------
`convergence_registry.record_convergence_snapshot()` incrementa
`confirmation_count` en cada re-escaneo. Eso NO es evidencia nueva: el mismo
snapshot repetido 10.000 veces no ha observado nada. Por eso la unidad causal
no es la confirmación sino la REVISIÓN, identificada por
`evidence_revision_hash`, calculada EXCLUSIVAMENTE sobre evidencia sustantiva,
excluyendo a propósito `confirmation_count` y toda marca de tiempo. Reejecutar
el mismo snapshot no puede aumentar evidencia, confianza ni aprendizaje.

APRENDER NO CONCEDE PERMISO PARA EJECUTAR
-----------------------------------------
Este módulo produce EVIDENCIA para el criterio. No crea ejecutores, no altera
límites monetarios, no cambia paper/real, no elimina condiciones del validador
de entradas, no salta autorización, governor, controles de riesgo ni pausas.
Esos controles siguen exactamente donde estaban.

LO QUE ESTE MÓDULO **NO** ES
----------------------------
No es `vault/learned_rules.jsonl`. Ese almacén contiene reglas COGNITIVAS
derivadas de hipótesis sobre el propio código (`core/learn/active_learning.py`
observa `WATCHED_DIRS = core,pipeline,cli,models,services`). Mezclar
aprendizaje operativo de dominio ahí sería juntar dos ciclos que no comparten
ni productor ni semántica. Este módulo tiene su propio almacén:
`<vault>/causal_learning.db`.

Creado: 2026-09-22 — PR 2, puente causal en producción directa.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.learn import VAULT_DIR

logger = logging.getLogger("vectrax.learn.causal_learning")


# ---------------------------------------------------------------------------
# Estados causales
# ---------------------------------------------------------------------------

STATE_OBSERVING = "OBSERVING"                      # hay señal, no hay convergencia
STATE_CONVERGED_CANDIDATE = "CONVERGED_CANDIDATE"  # convergió; NO cumple el gate todavía
STATE_AWAITING_POLICY = "AWAITING_POLICY"          # el dominio no declara política
STATE_LEARNED = "LEARNED"                          # superó el gate; AFECTA al criterio
STATE_WEAKENED = "WEAKENED"                        # la convergencia se disolvió
STATE_CONTRADICTED = "CONTRADICTED"                # outcomes verificados contrarios
STATE_OBSOLETE = "OBSOLETE"                        # retirado explícitamente

STATES = (
    STATE_OBSERVING, STATE_CONVERGED_CANDIDATE, STATE_AWAITING_POLICY,
    STATE_LEARNED, STATE_WEAKENED, STATE_CONTRADICTED, STATE_OBSOLETE,
)

#: Único estado que el criterio efectivo puede consumir. Solo con un
#: `LearningTrace` en este estado puede Vectrax decir "aprendí".
CONSUMABLE_STATES = (STATE_LEARNED,)

#: Procedencia del registro. Nunca hay backfill masivo: las 121.206
#: convergencias históricas y `convergence_history.db` no se recorren.
CREATED_FROM_LIVE = "live"
CREATED_FROM_EXPLICIT = "explicit_evaluation"
CREATED_FROM_TEST = "test"
CREATED_FROM = (CREATED_FROM_LIVE, CREATED_FROM_EXPLICIT, CREATED_FROM_TEST)

#: Resultados reconocidos, ya normalizados.
OUTCOME_WIN = "win"
OUTCOME_LOSS = "loss"
OUTCOME_NEUTRAL = "neutral"
VALID_OUTCOMES = (OUTCOME_WIN, OUTCOME_LOSS, OUTCOME_NEUTRAL)

#: Alias que producen los distintos productores del sistema. Se normalizan a
#: una sola forma para que "closed_loss" y "loss" no sean dos cosas distintas
#: al decidir si un aprendizaje se debilita.
_OUTCOME_ALIASES = {
    "win": OUTCOME_WIN, "success": OUTCOME_WIN, "ok": OUTCOME_WIN,
    "closed_win": OUTCOME_WIN, "profit": OUTCOME_WIN,
    "loss": OUTCOME_LOSS, "fail": OUTCOME_LOSS, "error": OUTCOME_LOSS,
    "closed_loss": OUTCOME_LOSS,
    "neutral": OUTCOME_NEUTRAL, "closed_neutral": OUTCOME_NEUTRAL,
    "expired": OUTCOME_NEUTRAL,
}


def normalize_outcome(raw: Any) -> str:
    """Normaliza el nombre de un resultado. Lanza si no se reconoce.

    Un nombre desconocido NO se degrada a neutral en silencio: un resultado que
    el puente no sabe interpretar no puede contarse como "no pasó nada".
    """
    key = str(raw or "").strip().lower()
    if key not in _OUTCOME_ALIASES:
        raise ValueError(
            f"outcome desconocido: {raw!r}. Reconocidos: "
            f"{sorted(_OUTCOME_ALIASES)}"
        )
    return _OUTCOME_ALIASES[key]


#: Tope de convergencias evaluadas por ciclo del observador. `meta_loop` no
#: puede quedarse bloqueado evaluando un escaneo global grande: lo que no entra
#: en este ciclo se evalúa en el siguiente, sin perderse (la identidad por
#: revisión hace que reencontrarlo sea idempotente).
MAX_EVALUATIONS_PER_CYCLE = 50


# ---------------------------------------------------------------------------
# Ruta del almacén — dinámica, nunca congelada en un argumento por defecto
# ---------------------------------------------------------------------------

DB_FILENAME = "causal_learning.db"


def _vault_dir() -> str:
    """Directorio del vault, resuelto EN CADA LLAMADA.

    Mismo convenio que `core/audit_ledger.py`, `observability/audit_engine.py`,
    `core/learn/verification_ledger.py` y `core/learn/learned_rules.py` tras
    PR #124. Sin `VECTRAX_VAULT_DIR` se usa `<raíz del proyecto>/vault`.
    """
    return os.environ.get("VECTRAX_VAULT_DIR") or VAULT_DIR


def default_db_path() -> str:
    return os.path.join(_vault_dir(), DB_FILENAME)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_policies (
    policy_id              TEXT NOT NULL,
    policy_version         INTEGER NOT NULL,
    domain                 TEXT NOT NULL,
    thresholds             TEXT NOT NULL DEFAULT '{}',
    threshold_provenance   TEXT NOT NULL DEFAULT '{}',
    created_at             REAL NOT NULL,
    PRIMARY KEY (policy_id, policy_version)
);
CREATE INDEX IF NOT EXISTS idx_policy_domain ON learning_policies(domain);

CREATE TABLE IF NOT EXISTS promotion_decisions (
    evaluation_id          TEXT PRIMARY KEY,
    convergence_id         TEXT NOT NULL,
    domain                 TEXT NOT NULL DEFAULT '',
    policy_id              TEXT,
    policy_version         INTEGER,
    eligible               INTEGER NOT NULL DEFAULT 0,
    state                  TEXT NOT NULL,
    reasons                TEXT NOT NULL DEFAULT '[]',
    metrics_observed       TEXT NOT NULL DEFAULT '{}',
    thresholds_applied     TEXT NOT NULL DEFAULT '{}',
    evidence_revision_hash TEXT NOT NULL,
    evaluated_at           REAL NOT NULL,
    created_from           TEXT NOT NULL DEFAULT 'live',
    lifecycle_event        TEXT NOT NULL DEFAULT ''
);
-- La IDENTIDAD de una revisión causal: una convergencia + un estado de
-- evidencia sustantivo. Repetir el mismo escaneo no crea filas nuevas.
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_revision
    ON promotion_decisions(convergence_id, evidence_revision_hash);
CREATE INDEX IF NOT EXISTS idx_decision_convergence
    ON promotion_decisions(convergence_id);

CREATE TABLE IF NOT EXISTS learning_traces (
    learning_id              TEXT PRIMARY KEY,
    claim                    TEXT NOT NULL DEFAULT '',
    domain                   TEXT NOT NULL DEFAULT '',
    state                    TEXT NOT NULL,
    source_convergence_id    TEXT NOT NULL,
    source_pattern_ids       TEXT NOT NULL DEFAULT '[]',
    evidence_ids             TEXT NOT NULL DEFAULT '[]',
    policy_id                TEXT,
    policy_version           INTEGER,
    confidence               REAL NOT NULL DEFAULT 0,
    metrics_at_promotion     TEXT NOT NULL DEFAULT '{}',
    thresholds_at_promotion  TEXT NOT NULL DEFAULT '{}',
    learned_at               REAL,
    last_validated_at        REAL,
    criterion_affected       TEXT NOT NULL DEFAULT '',
    criterion_version        TEXT NOT NULL DEFAULT '',
    data_scope               TEXT NOT NULL DEFAULT 'unknown',
    created_from             TEXT NOT NULL DEFAULT 'live',
    is_test_artifact         INTEGER NOT NULL DEFAULT 0,
    created_at               REAL NOT NULL,
    updated_at               REAL NOT NULL
);
-- Una convergencia bajo una política produce UN aprendizaje, no uno por scan.
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_identity
    ON learning_traces(source_convergence_id, policy_id);
CREATE INDEX IF NOT EXISTS idx_learning_domain ON learning_traces(domain, state);

CREATE TABLE IF NOT EXISTS learning_state_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    learning_id   TEXT NOT NULL,
    from_state    TEXT NOT NULL DEFAULT '',
    to_state      TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    evaluation_id TEXT NOT NULL DEFAULT '',
    timestamp     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_events_learning
    ON learning_state_events(learning_id, timestamp);

CREATE TABLE IF NOT EXISTS learning_applications (
    application_id    TEXT PRIMARY KEY,
    learning_id       TEXT NOT NULL,
    convergence_id    TEXT NOT NULL DEFAULT '',
    criterion_version TEXT NOT NULL DEFAULT '',
    decision_id       TEXT NOT NULL DEFAULT '',
    action_type       TEXT NOT NULL DEFAULT '',
    -- PAPER o LIVE. No es un modo de sombra: es el alcance real con el que se
    -- ejecutó la operación, y tiene que quedar distinguible en la traza.
    execution_scope   TEXT NOT NULL DEFAULT '',
    applied           INTEGER NOT NULL DEFAULT 1,
    abstained_reason  TEXT NOT NULL DEFAULT '',
    applied_at        REAL NOT NULL,
    outcome_id        TEXT,
    outcome_status    TEXT,
    outcome_value     REAL,
    resolved_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_application_learning
    ON learning_applications(learning_id);
CREATE INDEX IF NOT EXISTS idx_application_decision
    ON learning_applications(decision_id);

-- Trabajo que un ciclo no alcanzó a procesar. Solo se encola lo que NO puede
-- perderse: una disolución la emite el registro UNA vez (en la transición
-- activa->disuelta) y nunca vuelve a emitirla, así que si el presupuesto del
-- ciclo se agota antes de procesarla, se perdería para siempre. Las
-- convergencias vivas no se encolan: el escaneo las vuelve a emitir cada ciclo.
CREATE TABLE IF NOT EXISTS pending_work (
    convergence_id TEXT NOT NULL,
    domain         TEXT NOT NULL,
    kind           TEXT NOT NULL,
    payload        TEXT NOT NULL,
    enqueued_at    REAL NOT NULL,
    PRIMARY KEY (convergence_id, domain, kind)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_application_outcome
    ON learning_applications(outcome_id)
    WHERE outcome_id IS NOT NULL;
"""


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Abre el almacén y crea el esquema (idempotente).

    Crea el directorio y el esquema. Es la ruta de ESCRITURA: los lectores
    pasan por `_open_for_read()`, que devuelve `None` si el almacén todavía no
    existe en lugar de crearlo. Importar este módulo o calcular una ruta no
    toca disco.
    """
    path = db_path or default_db_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout: varios procesos (meta_loop, pipeline_worker) pueden
    # evaluar a la vez; las escrituras son pequeñas y siempre idempotentes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
    return conn


def _open_for_read(db_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
    """Conexión para LEER. Devuelve `None` si el almacén todavía no existe.

    Un lector nunca crea el almacén. Importa porque `criterion.py` consulta
    aprendizajes en CADA pregunta de criterio: si leer creara el archivo, una
    simple consulta sembraría `causal_learning.db` en el vault (o en el
    directorio que apuntara `VECTRAX_VAULT_DIR` en ese momento) como efecto
    secundario. El almacén lo crea quien escribe, que es el ciclo vivo.

    Si el archivo YA existe se pasa por `connect()`, que garantiza el esquema:
    un almacén de una versión anterior no puede hacer fallar una lectura.
    """
    path = db_path or default_db_path()
    if not os.path.exists(path):
        return None
    return connect(path)


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LearningPolicy:
    """Condiciones bajo las que una convergencia SE VUELVE aprendizaje.

    No existe una política universal implícita. Un dominio sin política
    registrada produce `AWAITING_POLICY`, nunca un aprendizaje por defecto.
    """
    policy_id: str
    domain: str
    thresholds: Mapping[str, float]
    required_metrics: Sequence[str] = ()
    policy_version: int = 1
    #: Origen documentado de CADA umbral: {métrica: "módulo.CONSTANTE (…)"}.
    #: Sin esto no se puede auditar si un número se reutilizó o se inventó.
    threshold_provenance: Mapping[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        missing = [m for m in self.required_metrics if m not in self.thresholds]
        if missing:
            raise ValueError(
                f"required_metrics sin umbral declarado: {missing}. "
                "No se asumen valores por defecto."
            )
        undocumented = [m for m in self.thresholds if m not in self.threshold_provenance]
        if undocumented:
            raise ValueError(
                f"umbrales sin origen documentado: {sorted(undocumented)}. "
                "Cada valor debe declarar de qué constante o contrato procede."
            )


@dataclass(frozen=True)
class PatternStats:
    """Métricas REALES de un patrón fuente, ancladas a su fingerprint.

    `sample_size`, `win_rate` y `expectancy` salen de
    `core.gravity_kernel.signals.fetch_pattern_stats()`, que las deriva del
    `outcome_history` de ESE fingerprint en el gravity index. No son una
    suposición ni un valor neutro: si un patrón no tiene historia graduada,
    `qualify_pattern()` devuelve `qualified=False` y el gate lo rechaza.
    """
    fingerprint: str
    sample_size: int = 0
    win_rate: float = 0.0       # PORCENTAJE [0, 100] — escala de domain_knowledge
    expectancy: float = 0.0
    confidence: float = 0.0     # [0, 1]
    qualified: bool = False
    reason: str = ""


@dataclass(frozen=True)
class CausalSnapshot:
    """La evidencia sustantiva de una convergencia en un instante.

    Es la ENTRADA de la evaluación y lo único que entra en el hash de revisión.
    """
    convergence_id: str
    domain: str
    source_pattern_ids: Sequence[str]
    evidence_ids: Sequence[str]
    metrics: Mapping[str, Any]
    status: str = "active"
    lifecycle_event: str = ""
    first_seen: float = 0.0
    claim: str = ""
    data_scope: str = "unknown"
    is_test_artifact: bool = False


@dataclass(frozen=True)
class PromotionDecision:
    evaluation_id: str
    convergence_id: str
    domain: str
    state: str
    eligible: bool
    reasons: Sequence[str]
    metrics_observed: Mapping[str, Any]
    thresholds_applied: Mapping[str, Any]
    evidence_revision_hash: str
    evaluated_at: float
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    created_from: str = CREATED_FROM_LIVE
    lifecycle_event: str = ""
    learning_id: Optional[str] = None
    #: True cuando esta evaluación NO creó revisión nueva porque la evidencia
    #: sustantiva es idéntica a una ya registrada.
    reused_existing_revision: bool = False


# ---------------------------------------------------------------------------
# Hash de revisión — el corazón del anti-falsa-repetición
# ---------------------------------------------------------------------------

#: Claves de `metrics` que NO entran en el hash. Son contadores de escaneo o
#: marcas de tiempo: cambian sin que se haya observado nada nuevo.
_NON_EVIDENTIAL_METRIC_KEYS = frozenset({
    "confirmation_count",   # se incrementa por re-escaneo, no por evidencia
    "last_seen",
    "first_seen",
    "scanned_at",
    "evaluated_at",
    "evidence_age_s",       # deriva del reloj, no de la observación
    "distinct_revisions",   # deriva del propio historial de revisiones
})


def compute_evidence_revision_hash(snapshot: CausalSnapshot) -> str:
    """Identidad de una REVISIÓN causal.

    Solo entra evidencia sustantiva: los patrones fuente, los ids de evidencia,
    el estado del ciclo de vida y las métricas medibles. Quedan FUERA
    `confirmation_count` y cualquier marca temporal — ver
    `_NON_EVIDENTIAL_METRIC_KEYS`.

    Consecuencia buscada: ejecutar el mismo snapshot 1 o 10.000 veces produce
    el mismo hash, y por tanto una sola revisión causal.
    """
    material = {
        "convergence_id": snapshot.convergence_id,
        "domain": snapshot.domain,
        "status": snapshot.status,
        "source_pattern_ids": sorted(str(p) for p in snapshot.source_pattern_ids),
        "evidence_ids": sorted(str(e) for e in snapshot.evidence_ids),
        "metrics": {
            k: _round_metric(v)
            for k, v in sorted(snapshot.metrics.items())
            if k not in _NON_EVIDENTIAL_METRIC_KEYS
        },
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _round_metric(value: Any) -> Any:
    """Redondea flotantes para que el ruido de coma flotante no cree revisiones."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    return value


# ---------------------------------------------------------------------------
# Cualificación de patrones fuente — el vínculo de identidad
# ---------------------------------------------------------------------------

def qualify_pattern(
    fingerprint: str, policy: LearningPolicy,
    stats_fetcher=None,
) -> PatternStats:
    """¿Este patrón fuente está cualificado con métricas REALES?

    EL VÍNCULO DE IDENTIDAD
    -----------------------
    Una convergencia identifica sus patrones fuente por el `fingerprint` del
    `GravityRecord` (`gravity_engine._match_pair` → `star_a` / `star_b`).
    `core.gravity_kernel.signals.fetch_pattern_stats()` acepta exactamente ese
    fingerprint y deriva `win_rate`, `expectancy` y `sample_size` del
    `outcome_history` de ESE registro. Es el mismo espacio de identidad: no
    hace falta inventar un puente ni sustituir las métricas por una suposición.

    NO se usa `core.domain_knowledge.AbstractPattern`, que vive en otro espacio
    de identidad (`signature`, biblioteca cross-tenant en
    `~/.vectrax/domain_library/`) y no es enlazable con un fingerprint de
    estrella. Sus UMBRALES sí se reutilizan (ver `build_production_policy`),
    porque su semántica es exactamente "este patrón es lo bastante sólido".

    ESCALAS
    -------
    `fetch_pattern_stats` devuelve `win_rate` como FRACCIÓN [0, 1];
    `domain_knowledge.MIN_WIN_RATE` es un PORCENTAJE (55.0). Aquí se convierte
    explícitamente a porcentaje para comparar en la misma escala.
    """
    if not fingerprint:
        return PatternStats(fingerprint="", reason="el patrón fuente no tiene identificador")

    fetcher = stats_fetcher
    if fetcher is None:
        try:
            from core.gravity_kernel.signals import fetch_pattern_stats as fetcher
        except Exception as exc:  # pragma: no cover - import defensivo
            logger.warning("qualify_pattern: no se pudo importar el proveedor: %s", exc)
            return PatternStats(
                fingerprint=fingerprint,
                reason="el proveedor de métricas de patrón no está disponible",
            )

    raw = fetcher(fingerprint)
    if not raw:
        return PatternStats(
            fingerprint=fingerprint,
            reason=(
                f"el patrón '{fingerprint}' no tiene outcomes graduados: "
                "no hay métricas reales con las que cualificarlo"
            ),
        )

    sample = int(float(raw.get("sample_size", 0) or 0))
    win_rate_pct = float(raw.get("win_rate", 0.0) or 0.0) * 100.0
    expectancy = float(raw.get("expectancy", 0.0) or 0.0)
    confidence = float(raw.get("confidence", 0.0) or 0.0)

    failures: List[str] = []
    min_sample = float(policy.thresholds.get("min_sample_size", 0))
    min_wr = float(policy.thresholds.get("min_win_rate", 0))
    min_exp = float(policy.thresholds.get("min_expectancy", 0))
    if sample < min_sample:
        failures.append(f"sample_size={sample} < {min_sample:.0f}")
    if win_rate_pct < min_wr:
        failures.append(f"win_rate={win_rate_pct:.1f}% < {min_wr:.1f}%")
    if expectancy <= min_exp:
        failures.append(f"expectancy={expectancy:.4f} <= {min_exp:.4f}")

    return PatternStats(
        fingerprint=fingerprint,
        sample_size=sample,
        win_rate=round(win_rate_pct, 2),
        expectancy=round(expectancy, 4),
        confidence=round(confidence, 4),
        qualified=not failures,
        reason="" if not failures else (
            f"el patrón '{fingerprint}' no está cualificado: " + "; ".join(failures)
        ),
    )


def pattern_evidence(
    source_pattern_ids: Sequence[str], policy: "LearningPolicy",
    stats_fetcher=None,
) -> Tuple[List[PatternStats], Dict[str, Any]]:
    """Cualifica todos los patrones fuente y resume su evidencia.

    El resumen entra en `metrics` ANTES de calcular el hash de revisión, y eso
    es deliberado: la maduración de un patrón (outcomes nuevos que suben su
    win_rate o su sample_size) ES evidencia sustantiva nueva. Si no entrara en
    el hash, un `CONVERGED_CANDIDATE` rechazado por muestra insuficiente se
    quedaría congelado para siempre — el escaneo repetido produciría la misma
    revisión y nunca se volvería a evaluar, ni siquiera cuando el patrón ya
    cumpliera el umbral.
    """
    stats = [
        qualify_pattern(str(fp), policy, stats_fetcher=stats_fetcher)
        for fp in source_pattern_ids if str(fp).strip()
    ]
    summary: Dict[str, Any] = {
        "qualified_patterns": [s.fingerprint for s in stats if s.qualified],
    }
    if stats and all(s.qualified for s in stats):
        # Confianza desde la evidencia real del patrón MÁS DÉBIL (contrato de
        # `fetch_pattern_stats`: graded/20). No se promedia, para que un patrón
        # fuerte no tape a uno débil.
        summary["pattern_confidence"] = round(min(s.confidence for s in stats), 4)
    if stats:
        summary["min_pattern_sample_size"] = min(s.sample_size for s in stats)
        summary["min_pattern_win_rate"] = min(s.win_rate for s in stats)
        summary["min_pattern_expectancy"] = min(s.expectancy for s in stats)
    return stats, summary


# ---------------------------------------------------------------------------
# Políticas de producción
# ---------------------------------------------------------------------------

#: Dominios operativos con productor real (ver la auditoría del 2026-09-22).
#: `florida_real_estate` es la clave REAL del dominio inmobiliario en el
#: código (`internal_evidence._AUDIT_ACTION_DOMAIN`), no `real_estate`.
PRODUCTION_DOMAINS = (
    "market",
    "freight_logistics",
    "florida_real_estate",
    "cybersecurity",
)


def build_production_policy(domain: str) -> LearningPolicy:
    """Política EFECTIVA del dominio. Cada umbral procede de una constante real.

    Reutiliza dos familias de constantes cuya semántica ya es exactamente la
    que el gate necesita, sin inventar ningún número:

    Cualificación de CADA patrón fuente — `core/domain_knowledge.py`, cuyos
    umbrales definen literalmente cuándo un patrón es lo bastante sólido para
    elevarse a conocimiento de dominio:
      * `MIN_SAMPLE = 15`
      * `MIN_WIN_RATE = 55.0`  (porcentaje)
      * `MIN_EXPECTANCY = 0.0` (comparación estricta `>`)

    Fuerza de la CONVERGENCIA — `core/learn/gravity_engine.py`, el motor que
    produce los candidatos, con estos comentarios literales en el código:
      * `ALERT_MIN_HITS = 5`  ("combined hits to consider significant")
      * `ALERT_MIN_CC  = 0.4` ("combined coherence to consider strong")

    `minimum_distinct_evidence_revisions = 1` NO es una espera artificial: la
    protección contra la falsa repetición es el `evidence_revision_hash` (un
    re-escaneo produce la MISMA revisión y por tanto no añade evidencia ni
    confianza), no un periodo de gracia. Exigir 2 revisiones impediría aprender
    en el primer ciclo aunque la evidencia ya fuera suficiente, que es
    justamente lo contrario de lo que se pide. La barra real la ponen las
    métricas de los patrones, que sí son evidencia observada.
    """
    from core.domain_knowledge import MIN_EXPECTANCY, MIN_SAMPLE, MIN_WIN_RATE
    from core.learn.gravity_engine import ALERT_MIN_CC, ALERT_MIN_HITS

    return LearningPolicy(
        policy_id=f"production-{domain}",
        domain=domain,
        required_metrics=("combined_hits", "combined_cc"),
        thresholds={
            "combined_hits": float(ALERT_MIN_HITS),
            "combined_cc": float(ALERT_MIN_CC),
            "min_sample_size": float(MIN_SAMPLE),
            "min_win_rate": float(MIN_WIN_RATE),
            "min_expectancy": float(MIN_EXPECTANCY),
            "minimum_distinct_evidence_revisions": 1.0,
        },
        threshold_provenance={
            "combined_hits": (
                "core.learn.gravity_engine.ALERT_MIN_HITS=5 "
                "('combined hits to consider significant')"
            ),
            "combined_cc": (
                "core.learn.gravity_engine.ALERT_MIN_CC=0.4 "
                "('combined coherence to consider strong')"
            ),
            "min_sample_size": "core.domain_knowledge.MIN_SAMPLE=15",
            "min_win_rate": "core.domain_knowledge.MIN_WIN_RATE=55.0 (porcentaje)",
            "min_expectancy": "core.domain_knowledge.MIN_EXPECTANCY=0.0 (estricto >)",
            "minimum_distinct_evidence_revisions": (
                "1 = la revisión actual. El anti-falsa-repetición es el "
                "evidence_revision_hash, no una espera; ver build_production_policy"
            ),
        },
    )


def ensure_production_policies(db_path: Optional[str] = None) -> List[str]:
    """Registra las políticas de los dominios operativos. Idempotente.

    Se llama desde el observador vivo, de modo que tras el despliegue los
    cuatro dominios tienen política ACTIVA y ninguno queda en
    `AWAITING_POLICY`. No registra políticas para dominios sin productor real.
    """
    registered = []
    for domain in PRODUCTION_DOMAINS:
        existing = get_policy(domain, db_path=db_path)
        policy = build_production_policy(domain)
        if existing is not None and dict(existing.thresholds) == dict(policy.thresholds):
            continue
        if existing is not None:
            # Un cambio de umbrales es una política NUEVA, versionada: la
            # anterior se conserva para poder auditar con qué umbral se
            # promovió cada aprendizaje histórico.
            policy = LearningPolicy(
                policy_id=policy.policy_id,
                domain=policy.domain,
                thresholds=policy.thresholds,
                required_metrics=policy.required_metrics,
                policy_version=existing.policy_version + 1,
                threshold_provenance=policy.threshold_provenance,
            )
        register_policy(policy, db_path=db_path)
        registered.append(domain)
    return registered


def register_policy(policy: LearningPolicy, db_path: Optional[str] = None) -> str:
    """Registra (o reemplaza) una versión de política. Idempotente."""
    conn = connect(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO learning_policies
               (policy_id, policy_version, domain, thresholds,
                threshold_provenance, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                policy.policy_id, policy.policy_version, policy.domain,
                json.dumps(dict(policy.thresholds)),
                json.dumps(dict(policy.threshold_provenance)),
                float(policy.created_at),
            ),
        )
        conn.commit()
        return policy.policy_id
    finally:
        conn.close()


def get_policy(domain: str, db_path: Optional[str] = None) -> Optional[LearningPolicy]:
    """Política vigente (mayor `policy_version`) del dominio, o `None`.

    `None` NO significa "usa los valores por defecto": significa que el dominio
    todavía no declara bajo qué condiciones converger implica aprender, y la
    evaluación terminará en `AWAITING_POLICY`.
    """
    conn = _open_for_read(db_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM learning_policies WHERE domain=? "
            "ORDER BY policy_version DESC LIMIT 1",
            (domain,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    thresholds = json.loads(row["thresholds"])
    return LearningPolicy(
        policy_id=row["policy_id"],
        domain=row["domain"],
        thresholds=thresholds,
        required_metrics=tuple(
            m for m in ("combined_hits", "combined_cc") if m in thresholds
        ),
        policy_version=int(row["policy_version"]),
        threshold_provenance=json.loads(row["threshold_provenance"]),
        created_at=float(row["created_at"]),
    )


def list_policies(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM learning_policies ORDER BY domain, policy_version DESC"
        ).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["thresholds"] = json.loads(r["thresholds"])
        r["threshold_provenance"] = json.loads(r["threshold_provenance"])
    return rows


# ---------------------------------------------------------------------------
# Evaluación — EL GATE
# ---------------------------------------------------------------------------

def _distinct_revisions(conn, convergence_id: str, domain: Optional[str] = None) -> int:
    """Revisiones de evidencia distintas de una convergencia.

    Se acota por dominio: una convergencia cruzada se evalúa una vez por cada
    dominio participante (ver `evaluate_live_convergences`), y la evaluación de
    `market` no es una revisión adicional de la evidencia de
    `freight_logistics`. Sin este acotado, el primer dominio inflaría el
    contador del segundo.
    """
    if domain is None:
        row = conn.execute(
            "SELECT COUNT(DISTINCT evidence_revision_hash) AS c "
            "FROM promotion_decisions WHERE convergence_id=?",
            (convergence_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(DISTINCT evidence_revision_hash) AS c "
            "FROM promotion_decisions WHERE convergence_id=? AND domain=?",
            (convergence_id, domain),
        ).fetchone()
    return int(row["c"] or 0)


def _existing_decision(conn, convergence_id: str, revision: str):
    return conn.execute(
        "SELECT * FROM promotion_decisions "
        "WHERE convergence_id=? AND evidence_revision_hash=?",
        (convergence_id, revision),
    ).fetchone()


def _existing_learning(conn, convergence_id: str, policy_id: Optional[str]):
    return conn.execute(
        "SELECT * FROM learning_traces "
        "WHERE source_convergence_id=? AND policy_id IS ?",
        (convergence_id, policy_id),
    ).fetchone()


def evaluate_convergence(
    snapshot: CausalSnapshot,
    *,
    created_from: str = CREATED_FROM_LIVE,
    stats_fetcher=None,
    db_path: Optional[str] = None,
) -> PromotionDecision:
    """Evalúa UNA convergencia contra el gate y persiste decisión y traza.

    Idempotente por `(convergence_id, evidence_revision_hash)`: si la evidencia
    sustantiva no cambió, devuelve la decisión ya registrada y no escribe nada
    nuevo (`reused_existing_revision=True`). Esa es la garantía de que un
    snapshot repetido no aumenta evidencia, confianza ni aprendizaje.
    """
    if created_from not in CREATED_FROM:
        raise ValueError(f"created_from inválido: {created_from!r}")

    now = time.time()
    policy = get_policy(snapshot.domain, db_path=db_path)

    # La evidencia de los patrones fuente forma parte de la REVISIÓN (ver
    # `pattern_evidence`), así que se cualifica antes de hashear y se reutiliza
    # en el gate: una sola lectura del proveedor por evaluación.
    stats: List[PatternStats] = []
    if policy is not None:
        stats, summary = pattern_evidence(
            snapshot.source_pattern_ids, policy, stats_fetcher,
        )
        snapshot = replace(snapshot, metrics={**dict(snapshot.metrics), **summary})

    revision = compute_evidence_revision_hash(snapshot)
    conn = connect(db_path)
    try:
        prior = _existing_decision(conn, snapshot.convergence_id, revision)
        if prior is not None:
            # Misma evidencia -> misma revisión. Ni fila nueva ni estado nuevo.
            learning = _existing_learning(
                conn, snapshot.convergence_id, prior["policy_id"],
            )
            return _decision_from_row(prior, learning, reused=True)

        metrics = dict(snapshot.metrics)
        metrics["distinct_revisions"] = _distinct_revisions(
            conn, snapshot.convergence_id, snapshot.domain,
        ) + 1
        if snapshot.first_seen:
            metrics["evidence_age_s"] = max(0.0, now - float(snapshot.first_seen))

        state, eligible, reasons, thresholds = _decide(
            snapshot, policy, metrics, conn, stats,
        )

        # Un aprendizaje CONTRADICHO u OBSOLETO no revive porque la
        # convergencia vuelva a aparecer (ver `_next_state`). La decisión debe
        # reportar el estado EFECTIVO, no la propuesta del gate: si dijera
        # LEARNED mientras la traza persistida sigue CONTRADICTED, cualquier
        # consumidor creería que se aprendió algo que no se aprendió.
        prior_learning = _existing_learning(
            conn, snapshot.convergence_id, policy.policy_id if policy else None,
        )
        if prior_learning is not None:
            effective = _next_state(prior_learning["state"], state)
            if effective != state:
                reasons.append(
                    f"el aprendizaje está en {prior_learning['state']}: una "
                    "reaparición no lo reactiva; hace falta retirada explícita"
                )
                state, eligible = effective, False

        evaluation_id = f"EVAL-{uuid.uuid4().hex[:12].upper()}"
        conn.execute(
            """INSERT INTO promotion_decisions
               (evaluation_id, convergence_id, domain, policy_id, policy_version,
                eligible, state, reasons, metrics_observed, thresholds_applied,
                evidence_revision_hash, evaluated_at, created_from,
                lifecycle_event)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evaluation_id, snapshot.convergence_id, snapshot.domain,
                policy.policy_id if policy else None,
                policy.policy_version if policy else None,
                1 if eligible else 0, state,
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False, default=str),
                json.dumps(thresholds, ensure_ascii=False, default=str),
                revision, now, created_from, snapshot.lifecycle_event,
            ),
        )
        learning_id = _upsert_learning(
            conn, snapshot, policy, state, metrics, thresholds,
            evaluation_id, created_from, now,
        )
        conn.commit()
        return PromotionDecision(
            evaluation_id=evaluation_id,
            convergence_id=snapshot.convergence_id,
            domain=snapshot.domain,
            state=state,
            eligible=eligible,
            reasons=reasons,
            metrics_observed=metrics,
            thresholds_applied=thresholds,
            evidence_revision_hash=revision,
            evaluated_at=now,
            policy_id=policy.policy_id if policy else None,
            policy_version=policy.policy_version if policy else None,
            created_from=created_from,
            lifecycle_event=snapshot.lifecycle_event,
            learning_id=learning_id,
        )
    finally:
        conn.close()


def _decide(snapshot, policy, metrics, conn, pattern_stats=None):
    """EL GATE. Diez condiciones. Nunca inventa un umbral que no exista.

    Devuelve `(state, eligible, reasons, thresholds)`. `reasons` explica SIEMPRE
    por qué: tanto cuando promueve como cuando no. Un `CONVERGED_CANDIDATE`
    siempre puede decir qué le falta exactamente.
    """
    reasons: List[str] = []
    thresholds: Dict[str, Any] = {}

    # (1) convergence_id canónico.
    if not snapshot.convergence_id:
        reasons.append("la convergencia no tiene convergence_id canónico")
        return STATE_CONVERGED_CANDIDATE, False, reasons, thresholds

    # (2) Estado activo. Una convergencia disuelta DEBILITA el aprendizaje; no
    #     lo borra ni borra su historia.
    if snapshot.status == "dissolved":
        reasons.append(
            "la convergencia de origen se disolvió: el aprendizaje queda "
            "debilitado, no eliminado"
        )
        return STATE_WEAKENED, False, reasons, thresholds

    # (7) Ausencia de evidencia contraria vigente.
    #
    # Un resultado negativo DEBILITA. No contradice: `CONTRADICTED` se reserva
    # para una retirada explícita y auditada (`mark_contradicted`), porque
    # decidir cuántas pérdidas constituyen una contradicción es una política
    # que todavía no está definida, y asumir una sería inventarla.
    #
    # Que esto se derive del dato en cada evaluación —y no de una marca
    # pegajosa— es lo que hace que una revisión posterior siga dando WEAKENED
    # mientras la pérdida exista, sin necesidad de un flag aparte.
    if _has_negative_outcomes(conn, snapshot.convergence_id):
        reasons.append(
            "existe al menos un resultado negativo verificado sobre este "
            "aprendizaje: queda debilitado mientras siga registrado"
        )
        return STATE_WEAKENED, False, reasons, thresholds

    # (8) Política de aprendizaje del dominio. Sin política no hay gate que
    #     superar, y sin gate no puede haber aprendizaje.
    if policy is None:
        reasons.append(
            f"no existe una política de aprendizaje para el dominio "
            f"'{snapshot.domain}'"
        )
        return STATE_AWAITING_POLICY, False, reasons, thresholds

    # (6) Umbral declarado y persistido.
    thresholds = dict(policy.thresholds)

    # (3) Patrones fuente identificables.
    patterns = [str(p) for p in snapshot.source_pattern_ids if str(p).strip()]
    if len(patterns) < 2:
        reasons.append(
            f"una convergencia exige dos patrones fuente identificables; "
            f"hay {len(patterns)}"
        )

    # (5) Métricas REALES de AMBOS patrones + (9) confianza desde evidencia real.
    # Ya vienen cualificadas desde `evaluate_convergence` (entraron en el hash
    # de revisión); aquí solo se leen sus motivos de rechazo.
    for stats in (pattern_stats or []):
        if not stats.qualified:
            reasons.append(stats.reason)

    # Fuerza de la convergencia: métricas exigidas por la política.
    for metric in policy.required_metrics:
        observed = metrics.get(metric)
        if observed is None:
            reasons.append(f"la métrica exigida '{metric}' no fue observada")
            continue
        if float(observed) < float(policy.thresholds[metric]):
            reasons.append(
                f"{metric}={observed} < umbral {policy.thresholds[metric]}"
            )

    # (4) Evidencia sustantiva. `confirmation_count` NO cuenta: solo revisiones
    #     con evidencia distinta, medidas por `evidence_revision_hash`.
    revisions = int(metrics.get("distinct_revisions", 0))
    minimum = int(policy.thresholds.get("minimum_distinct_evidence_revisions", 1))
    if revisions < minimum:
        reasons.append(
            f"revisiones de evidencia distintas={revisions} < {minimum} "
            "(re-escanear la misma convergencia no cuenta)"
        )

    if reasons:
        # (10) Trazabilidad: el candidato sabe exactamente qué le falta.
        return STATE_CONVERGED_CANDIDATE, False, reasons, thresholds

    reasons.append(
        "supera el gate: ambos patrones fuente están cualificados con métricas "
        "reales y la convergencia alcanza los umbrales declarados"
    )
    return STATE_LEARNED, True, reasons, thresholds


def _has_negative_outcomes(conn, convergence_id: str) -> bool:
    """¿Algún resultado verificado en contra? Los nombres ya están normalizados."""
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM learning_applications a
           JOIN learning_traces t ON t.learning_id = a.learning_id
           WHERE t.source_convergence_id = ?
             AND a.outcome_status = ?""",
        (convergence_id, OUTCOME_LOSS),
    ).fetchone()
    return int(row["c"] or 0) > 0


def _upsert_learning(
    conn, snapshot, policy, state, metrics, thresholds,
    evaluation_id, created_from, now,
) -> Optional[str]:
    """Crea o actualiza la traza de aprendizaje. Idempotente por identidad.

    Una convergencia bajo una política tiene UN `learning_id`, se evalúe una
    vez o mil. Ninguna transición borra evidencia histórica: los cambios de
    estado se anotan en `learning_state_events`.
    """
    policy_id = policy.policy_id if policy else None
    existing = _existing_learning(conn, snapshot.convergence_id, policy_id)
    confidence = float(metrics.get("pattern_confidence", 0.0) or 0.0)

    if existing is None:
        learning_id = f"LRN-{uuid.uuid4().hex[:12].upper()}"
        conn.execute(
            """INSERT INTO learning_traces
               (learning_id, claim, domain, state, source_convergence_id,
                source_pattern_ids, evidence_ids, policy_id, policy_version,
                confidence, metrics_at_promotion, thresholds_at_promotion,
                learned_at, last_validated_at, criterion_affected,
                criterion_version, data_scope, created_from, is_test_artifact,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                learning_id, snapshot.claim, snapshot.domain, state,
                snapshot.convergence_id,
                json.dumps(list(snapshot.source_pattern_ids)),
                json.dumps(list(snapshot.evidence_ids)),
                policy_id, policy.policy_version if policy else None,
                confidence,
                json.dumps(metrics, ensure_ascii=False, default=str),
                json.dumps(thresholds, ensure_ascii=False, default=str),
                now if state == STATE_LEARNED else None,
                now,
                snapshot.domain if state == STATE_LEARNED else "",
                _criterion_version(snapshot, now) if state == STATE_LEARNED else "",
                snapshot.data_scope, created_from,
                1 if snapshot.is_test_artifact else 0,
                now, now,
            ),
        )
        _record_state_event(conn, learning_id, "", state,
                            "primera evaluación causal", evaluation_id, now)
        return learning_id

    learning_id = existing["learning_id"]
    prev_state = existing["state"]
    # `state` ya llega resuelto por `_next_state` en `evaluate_convergence`;
    # se vuelve a aplicar aquí para que la regla se cumpla aunque a esta
    # función se la llame desde otro sitio.
    next_state = _next_state(prev_state, state)

    # `*_at_promotion` significa literalmente "en el momento de la promoción":
    # se CONGELAN al alcanzar `LEARNED` y no se vuelven a tocar. Si una
    # disolución posterior los sobrescribiera con el `{}` que devuelve
    # `_decide()` en la rama de `WEAKENED`, se borraría la evidencia que
    # justificó el aprendizaje — exactamente lo que este módulo promete no
    # hacer. El histórico vivo por revisión ya vive en `promotion_decisions`,
    # que guarda métricas y umbrales de CADA una.
    promoting = next_state == STATE_LEARNED
    conn.execute(
        """UPDATE learning_traces
           SET state=?, source_pattern_ids=?, evidence_ids=?, confidence=?,
               metrics_at_promotion=?, thresholds_at_promotion=?,
               last_validated_at=?, learned_at=?, criterion_affected=?,
               criterion_version=?, updated_at=?
           WHERE learning_id=?""",
        (
            next_state,
            json.dumps(list(snapshot.source_pattern_ids)),
            json.dumps(list(snapshot.evidence_ids)),
            confidence if promoting else float(existing["confidence"]),
            json.dumps(metrics, ensure_ascii=False, default=str) if promoting
            else existing["metrics_at_promotion"],
            json.dumps(thresholds, ensure_ascii=False, default=str) if promoting
            else existing["thresholds_at_promotion"],
            now,
            (existing["learned_at"] or now) if promoting else existing["learned_at"],
            snapshot.domain if promoting else existing["criterion_affected"],
            (existing["criterion_version"] or _criterion_version(snapshot, now))
            if promoting else existing["criterion_version"],
            now, learning_id,
        ),
    )
    if next_state != prev_state:
        _record_state_event(
            conn, learning_id, prev_state, next_state,
            f"transición por evaluación {evaluation_id}", evaluation_id, now,
        )
    return learning_id


def _criterion_version(snapshot, now: float) -> str:
    """Versión del criterio que este aprendizaje inaugura.

    Determinista a partir de la convergencia y del instante de promoción, para
    poder responder "cuál era el criterio anterior y cuál es el nuevo".
    """
    material = f"{snapshot.convergence_id}|{snapshot.domain}|{now:.6f}"
    return "crit-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _next_state(prev_state: str, proposed: str) -> str:
    """Transición permitida.

    Regla explícita de Mario: una REAPARICIÓN no reactiva automáticamente un
    aprendizaje contradicho. `CONTRADICTED` y `OBSOLETE` son absorbentes salvo
    retirada explícita; solo evidencia contraria puede moverlos, nunca el mero
    hecho de que la convergencia vuelva a aparecer.
    """
    if prev_state in (STATE_CONTRADICTED, STATE_OBSOLETE):
        if proposed in (STATE_CONTRADICTED, STATE_OBSOLETE):
            return proposed
        return prev_state
    return proposed


def _record_state_event(conn, learning_id, from_state, to_state, reason,
                        evaluation_id, ts) -> None:
    conn.execute(
        """INSERT INTO learning_state_events
           (learning_id, from_state, to_state, reason, evaluation_id, timestamp)
           VALUES (?,?,?,?,?,?)""",
        (learning_id, from_state, to_state, reason, evaluation_id, ts),
    )


def _decision_from_row(row, learning_row, reused: bool) -> PromotionDecision:
    # El estado EFECTIVO es el de la traza viva, no el que la decisión congeló
    # cuando se registró. Si un resultado negativo debilitó el aprendizaje
    # después, reevaluar la MISMA revisión tiene que seguir diciendo WEAKENED;
    # devolver el LEARNED archivado resucitaría en la respuesta algo que ya no
    # es cierto.
    state = learning_row["state"] if learning_row is not None else row["state"]
    eligible = bool(row["eligible"]) and state == STATE_LEARNED
    reasons = json.loads(row["reasons"])
    if learning_row is not None and state != row["state"]:
        reasons = list(reasons) + [
            f"estado actual del aprendizaje: {state} "
            f"(la decisión de esta revisión se registró como {row['state']})"
        ]
    return PromotionDecision(
        evaluation_id=row["evaluation_id"],
        convergence_id=row["convergence_id"],
        domain=row["domain"],
        state=state,
        eligible=eligible,
        reasons=reasons,
        metrics_observed=json.loads(row["metrics_observed"]),
        thresholds_applied=json.loads(row["thresholds_applied"]),
        evidence_revision_hash=row["evidence_revision_hash"],
        evaluated_at=float(row["evaluated_at"]),
        policy_id=row["policy_id"],
        policy_version=row["policy_version"],
        created_from=row["created_from"],
        lifecycle_event=row["lifecycle_event"],
        learning_id=learning_row["learning_id"] if learning_row else None,
        reused_existing_revision=reused,
    )


# ---------------------------------------------------------------------------
# Entrada viva — el ciclo normal del observador
# ---------------------------------------------------------------------------

def cycle_stats_fetcher():
    """Proveedor de métricas de patrón con UNA sola lectura del gravity index.

    `fetch_pattern_stats` construye un `GravityIndex()` nuevo y llama a
    `get()`, que a su vez hace `_load()`: toma el lock y lee el índice ENTERO
    del disco. Una por fingerprint. Evaluar 50 convergencias de 2 patrones
    costaba 100 lecturas íntegras por ciclo — justo el bloqueo de `meta_loop`
    que el presupuesto existe para evitar.

    Aquí el índice se lee una vez y todas las consultas se sirven de ese
    snapshot, reutilizando `derive_pattern_stats`, que es el mismo cuerpo que
    usa la ruta de una sola consulta.

    Si el índice no se puede leer devuelve un proveedor que responde `None` a
    todo: ningún patrón cualifica y nada se promueve, que es el fallo seguro.
    """
    try:
        from core.gravity_kernel.signals import derive_pattern_stats
        from core.learn.gravity_engine import get_gravity_index
        records = get_gravity_index().load_raw()
    except Exception as exc:
        logger.warning("gravity index no disponible para este ciclo: %s", exc)
        return lambda fingerprint: None

    def _fetch(fingerprint: str):
        return derive_pattern_stats(records.get(fingerprint))
    return _fetch


def _last_evaluated_map(conn) -> Dict[Tuple[str, str], float]:
    """Última evaluación por (convergencia, dominio). Una sola consulta."""
    return {
        (r["convergence_id"], r["domain"]): float(r["last_at"] or 0.0)
        for r in conn.execute(
            "SELECT convergence_id, domain, MAX(evaluated_at) AS last_at "
            "FROM promotion_decisions GROUP BY convergence_id, domain"
        )
    }


def _enqueue_pending(conn, convergence_id, domain, kind, payload, ts) -> None:
    conn.execute(
        """INSERT INTO pending_work
           (convergence_id, domain, kind, payload, enqueued_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(convergence_id, domain, kind) DO UPDATE SET
             payload=excluded.payload""",
        (convergence_id, domain, kind, json.dumps(payload, default=str), ts),
    )


def _drain_pending(db_path: Optional[str]) -> List[Dict[str, Any]]:
    """Trabajo encolado por ciclos anteriores, lo más antiguo primero."""
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM pending_work ORDER BY enqueued_at ASC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            entry = json.loads(row["payload"])
        except Exception:
            continue
        entry["_pending_kind"] = row["kind"]
        entry["_pending_domain"] = row["domain"]
        out.append(entry)
    return out


def _clear_pending(conn, convergence_id, domain, kind) -> None:
    conn.execute(
        "DELETE FROM pending_work WHERE convergence_id=? AND domain=? AND kind=?",
        (convergence_id, domain, kind),
    )


#: Estados de entrada que NO pueden perderse: el registro los emite una sola
#: vez, en la transición, y nunca vuelve a emitirlos.
_MUST_NOT_LOSE = ("dissolved", "retired")


def evaluate_live_convergences(
    entries: Sequence[Mapping[str, Any]],
    *,
    limit: int = MAX_EVALUATIONS_PER_CYCLE,
    created_from: str = CREATED_FROM_LIVE,
    stats_fetcher=None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Evalúa las convergencias que ha visto ESTE ciclo. Nunca hace backfill.

    `entries` son convergencias canónicas del snapshot actual — las que el
    escaneo normal acaba de tocar. No se recorre la tabla histórica, ni las
    121.206 convergencias del incidente, ni `convergence_history.db`.

    EL PRESUPUESTO CUENTA EVALUACIONES NUEVAS, NO VISITAS
    ----------------------------------------------------
    Antes se descontaba presupuesto ANTES de evaluar, así que con 51
    convergencias las 50 primeras lo consumían entero aunque ya estuvieran
    evaluadas y su evidencia no hubiese cambiado: la 51 no entraba nunca. Ahora
    solo descuenta la evaluación que crea una REVISIÓN nueva. Revisitar una
    revisión ya registrada es una consulta indexada y no gasta presupuesto, de
    modo que un estado estacionario (nada cambió) procesa el ciclo completo.

    PROGRESO DURABLE, SIN POSTERGACIÓN PERMANENTE
    ---------------------------------------------
    Las entradas vivas se ordenan por "hace más tiempo que no se evalúa"
    —leído de `promotion_decisions`, así que sobrevive a un reinicio— y las
    nunca evaluadas van primero. Una convergencia que se quedó fuera por
    presupuesto encabeza el ciclo siguiente. No hay cursor que perder ni
    estado en memoria que un reinicio borre.

    NADA QUE NO PUEDA PERDERSE SE PIERDE
    ------------------------------------
    Una disolución la emite el registro UNA vez, en la transición
    activa->disuelta, y no vuelve a emitirla: si el presupuesto se agotara
    antes de procesarla se perdería para siempre y el criterio seguiría
    apoyándose en evidencia que ya no existe. Por eso las disoluciones y
    retiradas van primero y, si no caben, se ENCOLAN en `pending_work` y el
    ciclo siguiente las drena antes que nada. Las convergencias vivas no se
    encolan: el escaneo las vuelve a emitir.

    FALLO PARCIAL
    -------------
    Cada convergencia se evalúa aislada. Si una falla (base bloqueada, fila
    corrupta, proveedor de métricas caído) se registra en `errors` y el ciclo
    CONTINÚA con las demás: el almacén causal nunca puede tumbar al observador.
    """
    result: Dict[str, Any] = {
        "evaluated": 0, "learned": 0, "candidates": 0,
        "awaiting_policy": 0, "weakened": 0, "contradicted": 0,
        "reused": 0, "errors": [], "truncated": False,
        "budget_spent": 0, "deferred": 0, "enqueued": 0, "drained": 0,
    }

    pending = _drain_pending(db_path)
    result["drained"] = len(pending)
    work = list(pending) + list(entries)
    if not work:
        return result

    try:
        ensure_production_policies(db_path=db_path)
    except Exception as exc:
        # Sin políticas todo queda en AWAITING_POLICY, que es honesto; no se
        # aborta el ciclo por ello.
        logger.warning("no se pudieron asegurar las políticas de producción: %s", exc)
        result["errors"].append(f"ensure_production_policies: {exc}")

    if stats_fetcher is None:
        stats_fetcher = cycle_stats_fetcher()

    conn = _open_for_read(db_path)
    if conn is None:
        last_seen: Dict[Tuple[str, str], float] = {}
    else:
        try:
            last_seen = _last_evaluated_map(conn)
        finally:
            conn.close()

    # Aplanar a (entrada, dominio) para poder ordenar y presupuestar por unidad
    # real de evaluación.
    units: List[Tuple[Mapping[str, Any], str]] = []
    for entry in work:
        domains = [d for d in (entry.get("domains") or []) if d]
        if entry.get("_pending_domain"):
            domains = [entry["_pending_domain"]]
        for domain in dict.fromkeys(domains):
            units.append((entry, domain))

    def _priority(unit):
        entry, domain = unit
        urgent = 0 if str(entry.get("status")) in _MUST_NOT_LOSE else 1
        # Nunca evaluada -> 0.0 -> primero dentro de su grupo.
        return (urgent, last_seen.get((entry.get("convergence_id"), domain), 0.0))

    units.sort(key=_priority)

    budget = max(0, int(limit))
    ts = time.time()
    for entry, domain in units:
        convergence_id = str(entry.get("convergence_id") or "")
        must_not_lose = str(entry.get("status")) in _MUST_NOT_LOSE
        kind = entry.get("_pending_kind") or str(entry.get("status") or "")

        if budget <= 0:
            result["truncated"] = True
            result["deferred"] += 1
            if must_not_lose:
                # No se puede perder: se encola para el ciclo siguiente.
                try:
                    wconn = connect(db_path)
                    try:
                        payload = {k: v for k, v in entry.items()
                                   if not k.startswith("_pending_")}
                        _enqueue_pending(
                            wconn, convergence_id, domain, kind, payload, ts,
                        )
                        wconn.commit()
                        result["enqueued"] += 1
                    finally:
                        wconn.close()
                except Exception as exc:
                    logger.warning(
                        "no se pudo encolar %s/%s: %s", convergence_id, domain, exc,
                    )
                    result["errors"].append(f"enqueue {convergence_id}/{domain}: {exc}")
            continue

        try:
            if get_policy(domain, db_path=db_path) is None:
                result["awaiting_policy"] += 1
                if entry.get("_pending_kind"):
                    wconn = connect(db_path)
                    try:
                        _clear_pending(wconn, convergence_id, domain, kind)
                        wconn.commit()
                    finally:
                        wconn.close()
                continue
            snapshot = CausalSnapshot(
                convergence_id=convergence_id,
                domain=domain,
                source_pattern_ids=list(entry.get("source_pattern_ids") or []),
                evidence_ids=list(entry.get("evidence_ids") or []),
                metrics={
                    "combined_cc": float(entry.get("combined_cc") or 0.0),
                    "combined_hits": int(entry.get("combined_hits") or 0),
                },
                status=str(entry.get("status") or "active"),
                lifecycle_event=str(entry.get("lifecycle_event") or ""),
                first_seen=float(entry.get("first_seen") or 0.0),
                claim=str(entry.get("claim") or ""),
                data_scope=f"{domain}/live",
            )
            decision = evaluate_convergence(
                snapshot, created_from=created_from,
                stats_fetcher=stats_fetcher, db_path=db_path,
            )
        except Exception as exc:
            logger.warning(
                "evaluación causal fallida para %s/%s: %s",
                convergence_id, domain, exc,
            )
            result["errors"].append(f"{convergence_id}/{domain}: {exc}")
            continue

        # SOLO una revisión nueva consume presupuesto.
        if decision.reused_existing_revision:
            result["reused"] += 1
        else:
            budget -= 1
            result["budget_spent"] += 1

        if entry.get("_pending_kind"):
            try:
                wconn = connect(db_path)
                try:
                    _clear_pending(wconn, convergence_id, domain, kind)
                    wconn.commit()
                finally:
                    wconn.close()
            except Exception as exc:
                logger.warning("no se pudo desencolar %s: %s", convergence_id, exc)

        result["evaluated"] += 1
        if decision.state == STATE_LEARNED:
            result["learned"] += 1
        elif decision.state == STATE_CONVERGED_CANDIDATE:
            result["candidates"] += 1
        elif decision.state == STATE_AWAITING_POLICY:
            result["awaiting_policy"] += 1
        elif decision.state == STATE_WEAKENED:
            result["weakened"] += 1
        elif decision.state == STATE_CONTRADICTED:
            result["contradicted"] += 1
    return result


# ---------------------------------------------------------------------------
# Refuerzo / contradicción explícitos
# ---------------------------------------------------------------------------

def mark_contradicted(
    learning_id: str, reason: str = "", db_path: Optional[str] = None,
) -> bool:
    """Contradice un aprendizaje. No borra su historia."""
    return _force_state(learning_id, STATE_CONTRADICTED, reason, db_path)


def mark_obsolete(
    learning_id: str, reason: str = "", db_path: Optional[str] = None,
) -> bool:
    """Retirada explícita. Es el único camino fuera de un estado absorbente."""
    return _force_state(learning_id, STATE_OBSOLETE, reason, db_path)


def _force_state(learning_id, state, reason, db_path) -> bool:
    now = time.time()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT state FROM learning_traces WHERE learning_id=?", (learning_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE learning_traces SET state=?, updated_at=? WHERE learning_id=?",
            (state, now, learning_id),
        )
        _record_state_event(
            conn, learning_id, row["state"], state,
            reason or "retirada explícita", "", now,
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aplicación y outcome — idempotentes, con identidad completa
# ---------------------------------------------------------------------------

def record_application(
    learning_id: str,
    *,
    application_id: Optional[str] = None,
    convergence_id: str = "",
    criterion_version: str = "",
    decision_id: str = "",
    action_type: str = "",
    execution_scope: str = "",
    applied: bool = True,
    abstained_reason: str = "",
    applied_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> str:
    """Registra que un aprendizaje influyó en una decisión concreta.

    `applied=False` registra una ABSTENCIÓN: que el criterio se abstuvo aquí es
    tan parte de la traza causal como que actuara. Idempotente por
    `application_id`.
    """
    app_id = application_id or f"APP-{uuid.uuid4().hex[:12].upper()}"
    ts = applied_at if applied_at is not None else time.time()
    conn = connect(db_path)
    try:
        conn.execute(
            """INSERT INTO learning_applications
               (application_id, learning_id, convergence_id, criterion_version,
                decision_id, action_type, execution_scope, applied,
                abstained_reason, applied_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(application_id) DO UPDATE SET
                 learning_id=excluded.learning_id,
                 convergence_id=excluded.convergence_id,
                 criterion_version=excluded.criterion_version,
                 decision_id=excluded.decision_id,
                 action_type=excluded.action_type,
                 execution_scope=excluded.execution_scope,
                 applied=excluded.applied,
                 abstained_reason=excluded.abstained_reason""",
            (app_id, learning_id, convergence_id, criterion_version,
             decision_id, action_type, execution_scope, 1 if applied else 0,
             abstained_reason, ts),
        )
        conn.commit()
        return app_id
    finally:
        conn.close()


#: Marca para un dominio que NO tiene ejecutor: no se inventan aplicaciones.
NO_OPERATIONAL_CONSUMER = "NO_OPERATIONAL_CONSUMER"

#: Dominios con un consumidor operativo real capaz de aplicar o abstenerse.
#: `market` lo tiene (`connectors/etoro`). Los demás observan y aprenden, pero
#: ningún ejecutor consume su criterio todavía, así que una "aplicación" suya
#: sería inventada.
_DOMAINS_WITH_EXECUTOR = frozenset({"market"})


def operational_consumer(domain: str) -> str:
    """Quién puede APLICAR el criterio de este dominio.

    Devuelve `NO_OPERATIONAL_CONSUMER` cuando no hay ninguno. Decirlo es la
    respuesta correcta: fabricar aplicaciones para un dominio sin ejecutor
    llenaría la traza causal de acontecimientos que nunca ocurrieron.
    """
    if domain in _DOMAINS_WITH_EXECUTOR:
        return "connectors.etoro.learning_engine._auto_execute_proposals"
    return NO_OPERATIONAL_CONSUMER


def _application_id(decision_id: str, learning_id: str) -> str:
    """Identidad determinista: la misma decisión y el mismo aprendizaje dan el
    mismo id, así que reintentar el ciclo no duplica la aplicación."""
    material = f"{decision_id}|{learning_id}"
    return "APP-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper()


def record_decision(
    learning_ids: Sequence[str],
    *,
    decision_id: str,
    applied: bool,
    action_type: str = "",
    execution_scope: str = "",
    convergence_id: str = "",
    abstained_reason: str = "",
    applied_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> List[str]:
    """Registra que una decisión real estuvo influida por estos aprendizajes.

    Una aplicación por aprendizaje: cada uno tiene su propia influencia y su
    propio resultado, y mezclarlos impediría saber cuál se reforzó y cuál se
    desmintió. La identidad es determinista en `(decision_id, learning_id)`,
    así que reintentar el ciclo no duplica nada.

    `applied=False` registra una ABSTENCIÓN con su motivo: que el criterio se
    abstuviera aquí es tan parte de la traza como que actuara.

    Con `learning_ids` vacío no escribe nada y devuelve `[]`. Es lo correcto:
    si ningún aprendizaje influyó en la decisión, inventar una aplicación sería
    afirmar una causa que no existió.
    """
    if not learning_ids:
        return []
    ts = applied_at if applied_at is not None else time.time()
    created: List[str] = []
    for learning_id in learning_ids:
        if not learning_id:
            continue
        learning = get_learning(learning_id, db_path=db_path)
        created.append(record_application(
            learning_id,
            application_id=_application_id(decision_id, learning_id),
            convergence_id=convergence_id or (
                learning.get("source_convergence_id", "") if learning else ""
            ),
            criterion_version=(
                learning.get("criterion_version", "") if learning else ""
            ),
            decision_id=decision_id,
            action_type=action_type,
            execution_scope=execution_scope,
            applied=applied,
            abstained_reason=abstained_reason,
            applied_at=ts,
            db_path=db_path,
        ))
    return created


def resolve_decision_outcome(
    decision_id: str,
    *,
    outcome_status: str,
    outcome_value: Optional[float] = None,
    resolved_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> List[str]:
    """Cierra el resultado de TODAS las aplicaciones de una decisión.

    El ciclo que resuelve la operación conoce el `decision_id` (el
    `proposal_id` de la propuesta ejecutada), no los `application_id`. Esta
    función es el puente, y mantiene la idempotencia: una aplicación que ya
    tiene resultado no se reescribe.
    """
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT application_id FROM learning_applications "
            "WHERE decision_id=? AND applied=1 AND outcome_id IS NULL",
            (decision_id,),
        ).fetchall()
    finally:
        conn.close()
    resolved = []
    for row in rows:
        out = record_outcome(
            row["application_id"], outcome_status=outcome_status,
            outcome_value=outcome_value, resolved_at=resolved_at, db_path=db_path,
        )
        if out:
            resolved.append(out)
    return resolved


def record_outcome(
    application_id: str,
    *,
    outcome_id: Optional[str] = None,
    outcome_status: str = "",
    outcome_value: Optional[float] = None,
    resolved_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Registra el resultado POSTERIOR a aplicar un aprendizaje.

    Devuelve el `outcome_id`, o `None` si la aplicación no existe — nunca
    inventa una aplicación para colgarle un resultado.
    """
    status = normalize_outcome(outcome_status)
    out_id = outcome_id or f"OUT-{uuid.uuid4().hex[:12].upper()}"
    ts = resolved_at if resolved_at is not None else time.time()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT learning_id, outcome_id FROM learning_applications "
            "WHERE application_id=?",
            (application_id,),
        ).fetchone()
        if row is None:
            logger.warning(
                "record_outcome: application_id inexistente %s", application_id,
            )
            return None
        if row["outcome_id"]:
            # Idempotencia: un resultado ya registrado no se reescribe ni
            # vuelve a debilitar. Devolver el existente hace que reintentar el
            # ciclo de resolución sea inocuo.
            return row["outcome_id"]

        conn.execute(
            """UPDATE learning_applications
               SET outcome_id=?, outcome_status=?, outcome_value=?, resolved_at=?
               WHERE application_id=?""",
            (out_id, status, outcome_value, ts, application_id),
        )

        # EFECTO INMEDIATO. El primer resultado negativo verificado debilita el
        # aprendizaje AQUÍ, en la misma transacción — no en la próxima
        # evaluación. Esperar a la siguiente revisión causal significaba que un
        # aprendizaje ya desmentido seguía alimentando el criterio hasta que
        # cambiara la evidencia, y si la revisión no cambiaba, para siempre.
        #
        # Una pérdida debilita; NO contradice. `CONTRADICTED` sigue siendo una
        # transición explícita y auditada (`mark_contradicted`) hasta que exista
        # una política de contradicción: inventar aquí un umbral ("N pérdidas
        # seguidas") sería exactamente la clase de semántica que no nos
        # corresponde inventar.
        if status == OUTCOME_LOSS and row["learning_id"]:
            learning = conn.execute(
                "SELECT state FROM learning_traces WHERE learning_id=?",
                (row["learning_id"],),
            ).fetchone()
            if learning is not None and learning["state"] == STATE_LEARNED:
                conn.execute(
                    "UPDATE learning_traces SET state=?, updated_at=? "
                    "WHERE learning_id=?",
                    (STATE_WEAKENED, ts, row["learning_id"]),
                )
                _record_state_event(
                    conn, row["learning_id"], STATE_LEARNED, STATE_WEAKENED,
                    f"resultado negativo verificado ({out_id})", "", ts,
                )
        conn.commit()
        return out_id
    finally:
        conn.close()


def outcome_balance(
    learning_id: str, db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Refuerzos y contradicciones acumulados de un aprendizaje."""
    conn = _open_for_read(db_path)
    if conn is None:
        return {"reinforcing": 0, "contradicting": 0, "other": 0}
    try:
        rows = conn.execute(
            "SELECT outcome_status, COUNT(*) AS c FROM learning_applications "
            "WHERE learning_id=? AND outcome_id IS NOT NULL "
            "GROUP BY outcome_status",
            (learning_id,),
        ).fetchall()
    finally:
        conn.close()
    balance = {"reinforcing": 0, "contradicting": 0, "other": 0}
    for row in rows:
        status = (row["outcome_status"] or "").lower()
        count = int(row["c"])
        if status in ("win", "success", "ok"):
            balance["reinforcing"] += count
        elif status in ("loss", "fail", "error"):
            balance["contradicting"] += count
        else:
            balance["other"] += count
    return balance


# ---------------------------------------------------------------------------
# Lectores (solo lectura, nunca escriben)
# ---------------------------------------------------------------------------

def get_learning(learning_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _open_for_read(db_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM learning_traces WHERE learning_id=?", (learning_id,),
        ).fetchone()
        return _learning_to_dict(row) if row else None
    finally:
        conn.close()


def list_learnings(
    domain: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM learning_traces"
    clauses, params = [], []
    if domain:
        clauses.append("domain=?")
        params.append(domain)
    if state:
        clauses.append("state=?")
        params.append(state)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        return [_learning_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def consumable_learnings(
    domain: str, limit: int = 50, db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aprendizajes que el criterio efectivo PUEDE consumir.

    Solo `LEARNED`. Es la única puerta por la que el puente causal entra en el
    criterio, y por tanto la única base sobre la que Vectrax puede decir
    "aprendí".
    """
    return list_learnings(
        domain=domain, state=STATE_LEARNED, limit=limit, db_path=db_path,
    )


def get_decisions(
    convergence_id: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM promotion_decisions"
    clauses, params = [], []
    if convergence_id:
        clauses.append("convergence_id=?")
        params.append(convergence_id)
    if domain:
        clauses.append("domain=?")
        params.append(domain)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY evaluated_at DESC LIMIT ?"
    params.append(int(limit))
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["reasons"] = json.loads(r["reasons"])
        r["metrics_observed"] = json.loads(r["metrics_observed"])
        r["thresholds_applied"] = json.loads(r["thresholds_applied"])
        r["eligible"] = bool(r["eligible"])
    return rows


def get_applications(
    learning_id: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM learning_applications"
    params: List[Any] = []
    if learning_id:
        sql += " WHERE learning_id=?"
        params.append(learning_id)
    sql += " ORDER BY applied_at DESC LIMIT ?"
    params.append(int(limit))
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["applied"] = bool(r["applied"])
    return rows


def get_state_events(
    learning_id: str, limit: int = 50, db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM learning_state_events WHERE learning_id=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (learning_id, int(limit)),
        ).fetchall()]
    finally:
        conn.close()


def count_revisions(
    convergence_id: str, domain: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Revisiones causales DISTINTAS de una convergencia.

    No es `confirmation_count`: cuenta estados de evidencia distintos. Sin
    `domain` agrega todos los dominios participantes; con él responde por el
    criterio de ese dominio, que es la unidad que usa el gate.
    """
    conn = _open_for_read(db_path)
    if conn is None:
        return 0
    try:
        return _distinct_revisions(conn, convergence_id, domain)
    finally:
        conn.close()


def _learning_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["source_pattern_ids"] = json.loads(d["source_pattern_ids"])
    d["evidence_ids"] = json.loads(d["evidence_ids"])
    d["metrics_at_promotion"] = json.loads(d["metrics_at_promotion"])
    d["thresholds_at_promotion"] = json.loads(d["thresholds_at_promotion"])
    d["is_test_artifact"] = bool(d["is_test_artifact"])
    return d
