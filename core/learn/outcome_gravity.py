"""
core/learn/outcome_gravity.py — Del resultado VERIFICADO a la estrella que lo produjo.

El tramo que faltaba del ciclo de aprendizaje
---------------------------------------------
El recorrido completo que exige `core/learn/causal_learning.py` es::

    resultado verificado → patrón cualificado → convergencia LEARNED → criterio

El primer eslabón estaba roto. Los ciclos de verificación de market y freight
SÍ producían `Outcome` reales contra la verdad objetiva de su dominio y SÍ los
persistían en `core/learn/verification_ledger.py`, pero ese ledger está
indexado por ``(domain, subject)`` y nadie llevaba el veredicto hasta el
`GravityRecord` del patrón. `qualify_pattern()` interroga a
`signals.fetch_pattern_stats(fingerprint)`, que deriva win_rate / expectancy /
sample_size de la historia graduable de ESE registro. Sin nadie que la
escribiera, `derive_pattern_stats` devolvía ``None`` para toda estrella de
market y de freight, `qualify_pattern` respondía "no tiene outcomes
graduados", y NINGUNA convergencia podía pasar de CONVERGED_CANDIDATE a
LEARNED. El puente causal estaba construido y no le llegaba agua.

Este módulo es ese tramo, y nada más. No calcula resultados, no los interpreta
y no los corrige: recibe un `Outcome` ya resuelto por el `OutcomeAdapter` del
dominio y lo deposita en la estrella correcta.

Tres propiedades, que son exactamente lo que se pidió
------------------------------------------------------
1. **Fingerprint correcto.** El llamador aporta la identidad de la estrella
   calculada con la MISMA función que la creó (`domain_ingester.star_fingerprint`
   en freight; la convención literal ``market:{SÍMBOLO}`` de
   `connectors/etoro/learning_engine._feed_gravity` en market). Si esa estrella
   no existe, el resultado NO se aplica y NO se inventa un patrón para él:
   `GravityIndex.record_verified_outcomes()` devuelve False y aquí se cuenta
   como ``no_star``. Un resultado huérfano es un dato a explicar, no una
   estrella nueva.

2. **Procedencia.** Cada aplicación deja una fila en
   ``<vault>/outcome_gravity.db`` que dice qué predicción concreta
   (``prediction_id``), de qué dominio y sujeto, con qué veredicto y magnitud,
   aplicada por qué ciclo (``source``) y cuándo, aterrizó en qué estrella. La
   historia de la estrella guarda solo "win"/"loss" porque es lo que el
   graduador lee; esta tabla es la que permite responder "¿de dónde salió este
   win?" sin adivinar.

3. **Sin duplicados.** La clave primaria ``(fingerprint, prediction_id)`` y un
   ``INSERT OR IGNORE`` hacen de la propia inserción la prueba de novedad: si
   la fila ya estaba, ``rowcount`` es 0 y el resultado no se vuelve a contar.
   Es atómico y sirve entre procesos, a diferencia de un set en memoria o de
   un "¿ya lo vi?" leído antes de escribir. Importa porque la historia
   graduable está acotada (``MAX_OUTCOME_HISTORY``): re-aplicar el mismo
   resultado no solo lo contaría dos veces, además expulsaría uno distinto.

Orden de las dos escrituras
---------------------------
Se reclama la fila primero y se confirma la transacción DESPUÉS de que la
gravedad haya aceptado el resultado; si la estrella no existe, se deshace la
reclamación. Así un resultado que hoy no tiene estrella no queda marcado como
aplicado, y el siguiente ciclo puede volver a intentarlo cuando la estrella
exista. La ventana inversa (gravedad escrita y commit perdido por una caída
entre ambos) es la única que podría duplicar, y se prefiere a perder
resultados en silencio de forma permanente.

Solo se aplican veredictos DECISIVOS (WIN/LOSS). Un NEUTRAL no es un acierto
ni un fallo: `derive_pattern_stats` no lo contaría de ningún modo, y anotarlo
consumiría una plaza de una historia acotada sin aportar nada.

Este módulo NO toca umbrales, NO ejecuta nada y NO concede permisos: deposita
evidencia real donde el evaluador ya sabía buscarla.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.learn.outcome_adapter import Outcome, OutcomeStatus

logger = logging.getLogger("vectrax.outcome_gravity")

STORE_FILENAME = "outcome_gravity.db"

#: Ruta de PRODUCCIÓN cuando no hay override, idéntica al resto del vault.
PRODUCTION_VAULT_DIR = os.path.join(os.path.expanduser("~"), "Vectrax", "vault")

# ── Resultados posibles de aplicar un outcome ─────────────────────────
APPLIED = "applied"            # anotado en la estrella por primera vez
DUPLICATE = "duplicate"        # esta (fingerprint, prediction_id) ya se aplicó
NOT_DECISIVE = "not_decisive"  # NEUTRAL/PENDING: no es ni acierto ni fallo
NO_STAR = "no_star"            # el fingerprint no existe en el gravity index
NO_IDENTITY = "no_identity"    # falta fingerprint o prediction_id

RESULTS = (APPLIED, DUPLICATE, NOT_DECISIVE, NO_STAR, NO_IDENTITY)


def vault_dir() -> str:
    """Directorio del vault, resuelto EN CADA LLAMADA (nunca congelado en el
    import, mismo convenio que `core/audit_ledger.py` y el verification_ledger).
    """
    return os.environ.get("VECTRAX_VAULT_DIR") or PRODUCTION_VAULT_DIR


def store_path(db_path: Optional[str] = None) -> str:
    """Ruta del almacén de procedencia. `db_path` explícito gana (pruebas)."""
    return db_path or os.path.join(vault_dir(), STORE_FILENAME)


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS applied_outcomes (
    fingerprint    TEXT NOT NULL,
    prediction_id  TEXT NOT NULL,
    domain         TEXT NOT NULL DEFAULT '',
    subject        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    score          REAL NOT NULL DEFAULT 0.0,
    source         TEXT NOT NULL DEFAULT '',
    resolved_ts    REAL NOT NULL DEFAULT 0.0,
    applied_at     REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (fingerprint, prediction_id)
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_applied_outcomes_domain
    ON applied_outcomes (domain, subject);
"""


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Conexión de ESCRITURA: crea el directorio y la tabla si hacen falta."""
    path = store_path(db_path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    conn.commit()
    return conn


def _open_for_read(db_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
    """Conexión de LECTURA. `None` si el almacén todavía no existe.

    Un lector nunca crea el almacén: preguntar por la procedencia no puede
    sembrar un `outcome_gravity.db` vacío allí donde apunte `VECTRAX_VAULT_DIR`
    en ese instante.
    """
    path = store_path(db_path)
    if not os.path.exists(path):
        return None
    return _get_conn(path)


def _empty_counts() -> Dict[str, int]:
    return {k: 0 for k in RESULTS}


# ── Aplicación ────────────────────────────────────────────────────────

def apply_verified_outcomes(
    items: Sequence[Tuple[str, Outcome]],
    source: str,
    *,
    index: Any = None,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Lleva un lote de resultados verificados a sus estrellas.

    `items` son pares ``(fingerprint, outcome)``: la identidad de la estrella
    la decide el dominio, porque solo el dominio sabe qué patrón hizo esa
    predicción. Devuelve el recuento por resultado (ver `RESULTS`); la suma
    siempre iguala ``len(items)``.

    Nunca lanza: un fallo de almacenamiento deja el lote sin aplicar y se
    registra como WARNING —visible, no silencioso— porque el ciclo de
    verificación que llama aquí no puede romperse por esto. Lo que sí es
    inaceptable es aplicar a medias sin decirlo, y eso no ocurre: la
    transacción se confirma entera o no se confirma.
    """
    counts = _empty_counts()
    if not items:
        return counts

    # 1. Filtro previo, sin tocar disco: identidad e interés.
    candidates: List[Tuple[str, Outcome]] = []
    for fingerprint, outcome in items:
        if not fingerprint or not getattr(outcome, "prediction_id", ""):
            counts[NO_IDENTITY] += 1
            continue
        if not outcome.status.is_decisive:
            counts[NOT_DECISIVE] += 1
            continue
        candidates.append((fingerprint, outcome))

    if not candidates:
        return counts

    if index is None:
        try:
            from core.learn.gravity_engine import get_gravity_index
            index = get_gravity_index()
        except Exception as exc:
            logger.warning("outcome_gravity: gravity index no disponible: %s", exc)
            counts[NO_STAR] += len(candidates)
            return counts

    try:
        conn = _get_conn(db_path)
    except Exception as exc:
        logger.warning("outcome_gravity: almacén no disponible: %s", exc)
        counts[NO_STAR] += len(candidates)
        return counts

    now = time.time()
    try:
        # 2. Reclamar las filas (sin confirmar). `INSERT OR IGNORE` sobre la
        #    PK es la prueba de novedad: rowcount 0 = ya aplicado.
        claimed: List[Tuple[str, Outcome]] = []
        for fingerprint, outcome in candidates:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO applied_outcomes
                    (fingerprint, prediction_id, domain, subject, status,
                     score, source, resolved_ts, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    outcome.prediction_id,
                    outcome.domain,
                    outcome.subject,
                    outcome.status.value,
                    float(outcome.score or 0.0),
                    source,
                    float(getattr(outcome, "resolved_ts", 0.0) or 0.0),
                    now,
                ),
            )
            if cur.rowcount:
                claimed.append((fingerprint, outcome))
            else:
                counts[DUPLICATE] += 1

        # 3. Anotar en la gravedad (una sola transacción para todo el lote).
        if claimed:
            written = index.record_verified_outcomes(
                [(fp, o.status.value) for fp, o in claimed]
            )
            for (fingerprint, outcome), ok in zip(claimed, written):
                if ok:
                    counts[APPLIED] += 1
                else:
                    # Sin estrella: se deshace la reclamación para que el
                    # próximo ciclo pueda reintentarlo cuando exista.
                    counts[NO_STAR] += 1
                    conn.execute(
                        "DELETE FROM applied_outcomes "
                        "WHERE fingerprint = ? AND prediction_id = ?",
                        (fingerprint, outcome.prediction_id),
                    )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("outcome_gravity: lote no aplicado: %s", exc)
        return _empty_counts()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if counts[APPLIED] or counts[NO_STAR]:
        logger.info(
            "outcome_gravity | source=%s | applied=%d dup=%d no_star=%d "
            "not_decisive=%d no_identity=%d",
            source, counts[APPLIED], counts[DUPLICATE], counts[NO_STAR],
            counts[NOT_DECISIVE], counts[NO_IDENTITY],
        )
    return counts


def apply_verified_outcome(
    fingerprint: str,
    outcome: Outcome,
    source: str,
    *,
    index: Any = None,
    db_path: Optional[str] = None,
) -> str:
    """Caso de un solo resultado. Devuelve una de `RESULTS`."""
    counts = apply_verified_outcomes(
        [(fingerprint, outcome)], source, index=index, db_path=db_path,
    )
    for key in RESULTS:
        if counts[key]:
            return key
    return NO_STAR  # el lote se abortó (ver el WARNING del lote)


# ── Procedencia (lectura) ─────────────────────────────────────────────

_COLUMNS = (
    "fingerprint", "prediction_id", "domain", "subject", "status",
    "score", "source", "resolved_ts", "applied_at",
)


def provenance(
    *,
    fingerprint: Optional[str] = None,
    domain: Optional[str] = None,
    prediction_id: Optional[str] = None,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Qué resultados verificados alimentaron a qué estrellas, y de dónde
    vinieron. Es la respuesta auditable a "¿por qué este patrón cualificó?".
    """
    conn = _open_for_read(db_path)
    if conn is None:
        return []
    clauses: List[str] = []
    params: List[Any] = []
    if fingerprint:
        clauses.append("fingerprint = ?")
        params.append(fingerprint)
    if domain:
        clauses.append("domain = ?")
        params.append(domain)
    if prediction_id:
        clauses.append("prediction_id = ?")
        params.append(prediction_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM applied_outcomes{where} "
            "ORDER BY applied_at DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    except Exception as exc:
        logger.debug("outcome_gravity provenance failed: %s", exc)
        return []
    finally:
        conn.close()
    return [dict(zip(_COLUMNS, r)) for r in rows]


def applied_count(
    *, fingerprint: Optional[str] = None, domain: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Cuántos resultados verificados distintos se han aplicado."""
    conn = _open_for_read(db_path)
    if conn is None:
        return 0
    clauses: List[str] = []
    params: List[Any] = []
    if fingerprint:
        clauses.append("fingerprint = ?")
        params.append(fingerprint)
    if domain:
        clauses.append("domain = ?")
        params.append(domain)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM applied_outcomes{where}", tuple(params),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()
