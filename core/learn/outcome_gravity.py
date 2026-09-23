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

Orden de las dos escrituras, y por qué ninguna caída duplica
------------------------------------------------------------
Se reclama la fila primero y se confirma la transacción DESPUÉS de que la
gravedad haya aceptado el resultado.

Queda una ventana: si el proceso muere entre la escritura en gravedad y el
commit, el resultado está anotado pero no registrado. El reintento NO lo
duplica, porque la escritura en gravedad es idempotente por `prediction_id`
(ver `GravityIndex.record_verified_outcomes`): reconoce su propia escritura
anterior, no la repite, y el reintento se limita a confirmar el registro. Sin
eso, el reintento habría añadido el mismo resultado por segunda vez, inflando
el win_rate y expulsando otro resultado del final de la ventana acotada.

Un resultado cuya estrella todavía no existe NO se pierde ni se da por
aplicado: se APARCA en `pending_outcomes` y se reintenta en cada ciclo
posterior, hasta que la estrella aparezca. Esto importa porque el dedup de
market vive fuera de aquí —`run_market_verification` marca cada `signal_id`
como verificado y no vuelve a presentar esa señal—, así que "el siguiente
ciclo lo reintentará" solo es cierto si el reintento lo guarda ESTE módulo.
Antes no lo hacía, y una señal verificada antes de que su estrella existiera
quedaba perdida para siempre aunque la estrella apareciera después.

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
DEFERRED = "deferred"          # la estrella aún no existe: aparcado, se reintenta
RECOVERED = "recovered"        # aparcado antes, aplicado ahora que hay estrella
NO_IDENTITY = "no_identity"    # falta fingerprint o prediction_id
FAILED = "failed"              # el lote NO quedó contabilizado: reintentar

RESULTS = (APPLIED, DUPLICATE, NOT_DECISIVE, DEFERRED, RECOVERED,
           NO_IDENTITY, FAILED)

#: Estados en los que el resultado quedó DURABLEMENTE atendido: aplicado a la
#: estrella, reconocido como ya aplicado, aparcado para reintento, o descartado
#: por no ser graduable / no tener identidad. Todo lo demás es `FAILED`.
ACCOUNTED = (APPLIED, DUPLICATE, NOT_DECISIVE, DEFERRED, RECOVERED, NO_IDENTITY)


def accounted(counts: Dict[str, int], expected: int) -> bool:
    """¿Puede el llamador dar por atendidos los `expected` resultados del lote?

    Existe como función —y no como un `counts["failed"] == 0` suelto en cada
    llamador— porque de esta pregunta depende que market marque una señal como
    verificada y no la vuelva a presentar nunca. Un fallo de almacenamiento que
    el llamador confunda con "hecho" pierde el resultado para siempre.

    Antes había un estado `no_star` que se devolvía tanto cuando el índice de
    gravedad no estaba disponible como cuando el almacén fallaba por cualquier
    causa que no fuera un bloqueo —disco lleno, permisos, corrupción—. Market
    lo leía como un estado terminal normal y marcaba la señal igual. Ese estado
    ya no existe: lo que no queda atendido es `FAILED`, sin excepciones.

    `expected` es OBLIGATORIO, y esa es la segunda mitad de la protección. Sin
    él, la comprobación era solo "no hay fallos declarados", y un recuento
    VACÍO la pasaba: cero fallos porque cero de todo. Un recuento que no cubre
    el lote entero no es un éxito, es un recuento que no sabe lo que pasó —y
    esa distinción es justo la que este módulo lleva cinco revisiones
    aprendiendo a no perder. Se exige que la suma cubra exactamente el lote.
    """
    if expected < 0:
        return False
    if int(counts.get(FAILED, 0)) != 0:
        return False
    return sum(int(v) for v in counts.values()) == expected

#: Espera máxima a que otro escritor suelte la base, en milisegundos. SQLite
#: reintenta internamente durante este tiempo en vez de devolver
#: "database is locked" al primer intento.
BUSY_TIMEOUT_MS = 30000

#: Reintentos propios ante un bloqueo que sobreviva a `BUSY_TIMEOUT_MS`.
LOCK_RETRIES = 3

#: Umbral de AVISO para la tabla de aparcados. No borra nada.
#:
#: Antes esto era un tope que descartaba los aparcados más antiguos al
#: superarlo. Era un error, y contradecía el motivo mismo de este módulo: un
#: aparcado es un resultado VERIFICADO contra la verdad objetiva del dominio,
#: la evidencia más cara que produce el sistema. Descartarlo convierte un
#: problema visible —una tabla que crece porque las estrellas no se están
#: creando— en uno invisible: aprendizaje que se pierde sin que nadie lo note.
#: Y el criterio de descarte agravaba el error, porque los más antiguos son
#: precisamente los que más tiempo llevan acumulando derecho a ser aplicados.
#:
#: Ahora se avisa y se conserva. Una tabla que crece es el síntoma de otra
#: cosa (estrellas que no llegan a existir) y se arregla ahí, no borrando la
#: evidencia. `pending_outcomes()` permite ver exactamente qué espera y desde
#: cuándo.
PENDING_ALERT_THRESHOLD = 20000


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

#: Resultados decisivos verificados que TODAVÍA no tienen estrella. Misma clave
#: que `applied_outcomes`, para que un resultado no pueda estar en las dos.
_CREATE_PENDING = """
CREATE TABLE IF NOT EXISTS pending_outcomes (
    fingerprint    TEXT NOT NULL,
    prediction_id  TEXT NOT NULL,
    domain         TEXT NOT NULL DEFAULT '',
    subject        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    score          REAL NOT NULL DEFAULT 0.0,
    source         TEXT NOT NULL DEFAULT '',
    resolved_ts    REAL NOT NULL DEFAULT 0.0,
    deferred_at    REAL NOT NULL DEFAULT 0.0,
    last_attempt_at REAL NOT NULL DEFAULT 0.0,
    attempts       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (fingerprint, prediction_id)
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_applied_outcomes_domain
    ON applied_outcomes (domain, subject);
"""

#: El índice sigue al ORDEN DE REINTENTO (`last_attempt_at`), no al de llegada:
#: es la columna por la que `retry_pending` recorre la tabla.
_CREATE_PENDING_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pending_outcomes_retry
    ON pending_outcomes (domain, last_attempt_at);
"""


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Configura la base para que DOS ESCRITORES A LA VEZ no se tumben.

    Por defecto, SQLite usa un diario de rollback y un bloqueo de fichero que
    hace que el segundo escritor reciba `database is locked` en cuanto el
    primero tarde un poco. Con el ciclo de market y el de freight corriendo a
    la vez, eso ocurre: se reprodujo, y la segunda aplicación quedaba en cero.

    * ``journal_mode=WAL``: lectores y un escritor conviven sin bloquearse.
    * ``busy_timeout``: ante un bloqueo, SQLite espera y reintenta por su
      cuenta durante ese tiempo en vez de fallar al primer intento.
    * ``synchronous=NORMAL``: el compromiso habitual con WAL; no arriesga la
      integridad de la base, solo permite que el sistema agrupe los fsync.

    Nunca lanza: si el sistema de ficheros no admite WAL (algunos montajes de
    red), se sigue con el modo por defecto y queda el `busy_timeout`, que ya
    es la mayor parte de la protección.
    """
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
    except Exception as exc:  # pragma: no cover - depende del sistema
        logger.debug("outcome_gravity: busy_timeout no aplicado: %s", exc)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception as exc:  # pragma: no cover - depende del sistema
        logger.debug("outcome_gravity: WAL no disponible: %s", exc)


def _is_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Conexión de ESCRITURA: crea el directorio y la tabla si hacen falta."""
    path = store_path(db_path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000.0)
    _apply_pragmas(conn)
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_PENDING)
    _migrate_pending(conn)
    conn.execute(_CREATE_INDEX)
    conn.execute(_CREATE_PENDING_INDEX)
    conn.commit()
    return conn


def _migrate_pending(conn: sqlite3.Connection) -> None:
    """Anade `last_attempt_at` a una tabla creada antes de que existiera.

    Este almacen es nuevo y no esta desplegado, pero una copia de la rama
    anterior ya pudo crear la tabla sin esa columna; sin la migracion, el
    reintento fallaria con "no such column" y los aparcados quedarian
    inalcanzables — perder evidencia por un detalle de esquema seria el mismo
    fallo que esta correccion elimina. Se siembra con `deferred_at` para que
    el orden de reintento parta del de llegada.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_outcomes)")}
    if "last_attempt_at" in cols:
        return
    conn.execute(
        "ALTER TABLE pending_outcomes "
        "ADD COLUMN last_attempt_at REAL NOT NULL DEFAULT 0.0"
    )
    conn.execute("UPDATE pending_outcomes SET last_attempt_at = deferred_at")


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


#: Columnas comunes a `applied_outcomes` y `pending_outcomes` (la ultima difiere:
#: `applied_at` en una, `deferred_at` en la otra).
_COLUMNS = (
    "fingerprint", "prediction_id", "domain", "subject", "status",
    "score", "source", "resolved_ts", "applied_at",
)


def _empty_counts() -> Dict[str, int]:
    return {k: 0 for k in RESULTS}


# ── Aplicación ────────────────────────────────────────────────────────

def _balanced(counts: Dict[str, int], total: int) -> Dict[str, int]:
    """CONSERVACION DE RESULTADOS: cada elemento cae en exactamente un estado.

    Si la suma no cuadra con el tamano del lote, la diferencia se contabiliza
    como `FAILED`. Es la direccion segura: `FAILED` significa "reintentar", y
    un reintento es idempotente (la clave primaria y el `prediction_id` de la
    gravedad lo garantizan), mientras que dar por atendido algo que no lo esta
    pierde el resultado para siempre.

    Existe porque los dos ultimos fallos encontrados en revision fueron del
    mismo tipo: una ruta de error que devolvia un estado que el llamador leia
    como terminal. Este control convierte cualquier futuro descuadre —incluida
    una rama de error que alguien anada manana— en un reintento, no en una
    perdida silenciosa.
    """
    total_counted = sum(int(v) for v in counts.values())
    if total_counted == total:
        return counts
    missing = total - total_counted
    balanced = dict(counts)
    balanced[FAILED] = int(balanced.get(FAILED, 0)) + max(missing, 0)
    logger.warning(
        "outcome_gravity: el recuento no cuadra (%d contabilizados de %d); "
        "la diferencia se marca como FAILED para que se reintente",
        total_counted, total,
    )
    return balanced


def _outcome_ts(outcome: Outcome, fallback: float) -> float:
    """Instante en que se resolvio el resultado contra la verdad del dominio.

    Es lo que ORDENA la ventana acotada de la estrella, asi que un resultado
    recuperado del aparcadero ocupa el lugar que le corresponde por cuando
    ocurrio, no por cuando se pudo anotar.
    """
    try:
        ts = float(getattr(outcome, "resolved_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    return ts if ts > 0 else fallback


def _resolve_index(index: Any):
    if index is not None:
        return index
    from core.learn.gravity_engine import get_gravity_index
    return get_gravity_index()


def _defer(conn, fingerprint: str, outcome: Outcome, source: str, now: float) -> None:
    """Aparca un resultado cuya estrella todavia no existe.

    `attempts` se incrementa en cada reintento: no cambia la decision, pero
    deja ver cuanto lleva un resultado esperando a su patron.
    """
    conn.execute(
        """
        INSERT INTO pending_outcomes
            (fingerprint, prediction_id, domain, subject, status,
             score, source, resolved_ts, deferred_at, last_attempt_at, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(fingerprint, prediction_id)
        DO UPDATE SET attempts = attempts + 1, last_attempt_at = excluded.last_attempt_at
        """,
        (
            fingerprint, outcome.prediction_id, outcome.domain, outcome.subject,
            outcome.status.value, float(outcome.score or 0.0), source,
            float(getattr(outcome, "resolved_ts", 0.0) or 0.0), now, now,
        ),
    )


def _alert_if_pending_grows(conn) -> None:
    """Avisa si la tabla de aparcados crece. NO borra nada.

    Que haya muchos aparcados significa que hay estrellas que no se estan
    creando; el arreglo esta ahi, no en descartar los resultados que esperan.
    """
    row = conn.execute("SELECT COUNT(*) FROM pending_outcomes").fetchone()
    total = int(row[0]) if row else 0
    if total < PENDING_ALERT_THRESHOLD:
        return
    logger.warning(
        "outcome_gravity: %d resultados verificados esperando una estrella "
        "(umbral %d). NO se descarta ninguno: revisar por que no se estan "
        "creando esos patrones. Ver outcome_gravity.pending_outcomes().",
        total, PENDING_ALERT_THRESHOLD,
    )


def _commit_applied(conn, fingerprint: str, outcome: Outcome, source: str,
                    now: float) -> bool:
    """Reclama la fila de procedencia. False si ya estaba (duplicado)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO applied_outcomes
            (fingerprint, prediction_id, domain, subject, status,
             score, source, resolved_ts, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fingerprint, outcome.prediction_id, outcome.domain, outcome.subject,
            outcome.status.value, float(outcome.score or 0.0), source,
            float(getattr(outcome, "resolved_ts", 0.0) or 0.0), now,
        ),
    )
    return bool(cur.rowcount)


def apply_verified_outcomes(
    items: Sequence[Tuple[str, Outcome]],
    source: str,
    *,
    index: Any = None,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Lleva un lote de resultados verificados a sus estrellas.

    `items` son pares ``(fingerprint, outcome)``: la identidad de la estrella
    la decide el dominio, porque solo el dominio sabe que patron hizo esa
    prediccion. Devuelve el recuento por resultado (ver `RESULTS`).

    Un resultado decisivo cuya estrella no existe se APARCA (``deferred``), no
    se pierde: `retry_pending()` lo reintentara en ciclos posteriores.

    Nunca lanza: un fallo de almacenamiento deja el lote sin aplicar y se
    registra como WARNING —visible, no silencioso— porque el ciclo de
    verificacion que llama aqui no puede romperse por esto. Lo que si es
    inaceptable es aplicar a medias sin decirlo, y eso no ocurre: la
    transaccion se confirma entera o no se confirma.
    """
    counts = _empty_counts()
    if not items:
        return counts

    # 1. Filtro previo, sin tocar disco: identidad e interes.
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

    try:
        gravity = _resolve_index(index)
    except Exception as exc:
        logger.warning(
            "outcome_gravity: gravity index no disponible (%s); el lote NO "
            "queda contabilizado", exc,
        )
        counts[FAILED] += len(candidates)
        return counts

    for attempt in range(1, LOCK_RETRIES + 1):
        result = _apply_once(candidates, source, gravity, db_path, counts)
        if result is not None:
            return _balanced(result, len(items))
        if attempt < LOCK_RETRIES:
            time.sleep(0.2 * attempt)
    failed = _empty_counts()
    failed[FAILED] = len(items)
    logger.warning(
        "outcome_gravity: lote de %d NO contabilizado tras %d intentos "
        "(base bloqueada). El llamador NO debe darlo por verificado.",
        len(items), LOCK_RETRIES,
    )
    return failed


def _apply_once(
    candidates: List[Tuple[str, Outcome]],
    source: str,
    gravity: Any,
    db_path: Optional[str],
    base_counts: Dict[str, int],
) -> Optional[Dict[str, int]]:
    """Un intento de la transaccion. `None` si la base estaba bloqueada."""
    counts = dict(base_counts)
    try:
        conn = _get_conn(db_path)
    except Exception as exc:
        if _is_locked(exc):
            return None
        # Cualquier otro fallo del almacen —disco lleno, permisos, corrupcion—
        # deja el lote SIN contabilizar igual que un bloqueo. Devolver aqui un
        # estado que el llamador leyera como terminal perdia el resultado.
        logger.warning(
            "outcome_gravity: almacen no disponible (%s); el lote NO queda "
            "contabilizado", exc,
        )
        counts[FAILED] += len(candidates)
        return counts

    now = time.time()
    try:
        # 2. Reclamar las filas (sin confirmar). `INSERT OR IGNORE` sobre la
        #    PK es la prueba de novedad: rowcount 0 = ya aplicado.
        claimed: List[Tuple[str, Outcome]] = []
        for fingerprint, outcome in candidates:
            if _commit_applied(conn, fingerprint, outcome, source, now):
                claimed.append((fingerprint, outcome))
            else:
                counts[DUPLICATE] += 1

        # 3. Anotar en la gravedad (una sola transaccion para todo el lote).
        if claimed:
            written = gravity.record_verified_outcomes([
                (fp, o.status.value, o.prediction_id, _outcome_ts(o, now))
                for fp, o in claimed
            ])
            for (fingerprint, outcome), ok in zip(claimed, written):
                if ok:
                    counts[APPLIED] += 1
                    # Si venia de estar aparcado, ya no lo esta.
                    conn.execute(
                        "DELETE FROM pending_outcomes "
                        "WHERE fingerprint = ? AND prediction_id = ?",
                        (fingerprint, outcome.prediction_id),
                    )
                else:
                    # Sin estrella: se deshace la reclamacion y se aparca, para
                    # que el resultado siga existiendo cuando la estrella llegue.
                    counts[DEFERRED] += 1
                    conn.execute(
                        "DELETE FROM applied_outcomes "
                        "WHERE fingerprint = ? AND prediction_id = ?",
                        (fingerprint, outcome.prediction_id),
                    )
                    _defer(conn, fingerprint, outcome, source, now)
            _alert_if_pending_grows(conn)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        if _is_locked(exc):
            return None
        logger.warning("outcome_gravity: lote no aplicado: %s", exc)
        failed = _empty_counts()
        failed[FAILED] = len(candidates)
        return failed
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if counts[APPLIED] or counts[DEFERRED]:
        logger.info(
            "outcome_gravity | source=%s | applied=%d dup=%d deferred=%d "
            "not_decisive=%d no_identity=%d",
            source, counts[APPLIED], counts[DUPLICATE], counts[DEFERRED],
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
        if counts.get(key):
            return key
    return FAILED  # no se contabilizo en ningun estado: reintentar


def retry_pending(
    domain: Optional[str] = None,
    *,
    index: Any = None,
    db_path: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, int]:
    """Reintenta los resultados aparcados cuya estrella ya exista.

    DEBE llamarse una vez por ciclo, ANTES de cualquier salida temprana del
    ciclo de verificacion. La razon es que la deduplicacion de market vive
    fuera de este modulo: `run_market_verification` marca cada `signal_id`
    como verificado y no vuelve a presentar esa senal, asi que si el reintento
    dependiera de que llegara otro lote con esa misma senal, no ocurriria
    nunca. Un ciclo sin senales nuevas sigue teniendo que recuperar lo
    aparcado.

    RECORRIDO JUSTO, SIN BLOQUEO DE CABECERA
    ----------------------------------------
    Los candidatos se ordenan por `last_attempt_at` —el menos recientemente
    intentado primero—, NO por antiguedad de llegada, y a todo lo examinado se
    le sella el intento aunque siga sin estrella.

    Ordenar por `deferred_at` tenia un fallo grave: los mismos `limit`
    aparcados mas antiguos se elegian en cada ciclo, asi que si sus estrellas
    nunca llegaban a existir, ningun aparcado posterior se examinaba jamas —
    ni siquiera uno cuya estrella ya estaba creada. Un atasco permanente
    detras de un grupo bloqueado. Es el mismo defecto de inanicion que el
    presupuesto de convergencias del puente causal ya resolvio ordenando por
    el menos recientemente evaluado; aqui se repite la misma solucion.

    Con el sello, la tabla entera se recorre en `ceil(total / limit)` ciclos
    pase lo que pase con las estrellas, y el orden es estable entre reinicios
    porque `last_attempt_at` esta persistido.

    Devuelve el recuento por resultado; `recovered` son los que aterrizaron
    ahora. NUNCA lanza, ni siquiera si la base está bloqueada: el llamador la
    invoca por delante de su propio manejo de errores.
    """
    counts = _empty_counts()
    try:
        conn = _open_for_read(db_path)
    except Exception as exc:
        # La base puede estar tomada por otro escritor. Esto NO puede
        # propagarse: `retry_pending` es lo primero que hace
        # `run_market_verification`, por delante de su propio try, así que una
        # excepción aquí tumbaba la verificación entera del ciclo — un
        # bloqueo transitorio impedía además verificar las señales nuevas.
        # Los aparcados siguen en la tabla; el próximo ciclo los reintenta.
        counts[FAILED] = 1
        logger.warning(
            "outcome_gravity: no se pudo abrir el aparcadero (%s); "
            "el reintento se pospone al próximo ciclo", exc,
        )
        return counts
    if conn is None:
        return counts
    try:
        gravity = _resolve_index(index)
    except Exception as exc:
        logger.warning("outcome_gravity: gravity index no disponible: %s", exc)
        conn.close()
        return counts

    now = time.time()
    try:
        where, params = ("WHERE domain = ?", [domain]) if domain else ("", [])
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS[:-1])} FROM pending_outcomes {where} "
            "ORDER BY last_attempt_at ASC, rowid ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        if not rows:
            return counts

        parked = [(r[0], _outcome_from_row(r), r[6]) for r in rows]
        written = gravity.record_verified_outcomes([
            (fp, o.status.value, o.prediction_id, _outcome_ts(o, now))
            for fp, o, _src in parked
        ])
        for (fingerprint, outcome, row_source), ok in zip(parked, written):
            if not ok:
                # Sigue sin estrella. Se sella el intento IGUALMENTE: es lo que
                # hace que el proximo ciclo mire a los siguientes en vez de
                # volver a tropezar con estos mismos.
                counts[DEFERRED] += 1
                conn.execute(
                    "UPDATE pending_outcomes "
                    "SET attempts = attempts + 1, last_attempt_at = ? "
                    "WHERE fingerprint = ? AND prediction_id = ?",
                    (now, fingerprint, outcome.prediction_id),
                )
                continue
            counts[RECOVERED] += 1
            _commit_applied(conn, fingerprint, outcome, row_source, now)
            conn.execute(
                "DELETE FROM pending_outcomes "
                "WHERE fingerprint = ? AND prediction_id = ?",
                (fingerprint, outcome.prediction_id),
            )
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("outcome_gravity: reintento no aplicado: %s", exc)
        return _empty_counts()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if counts[RECOVERED]:
        logger.info(
            "outcome_gravity | reintento%s | recuperados=%d aun_sin_estrella=%d",
            f" domain={domain}" if domain else "", counts[RECOVERED],
            counts[DEFERRED],
        )
    return counts


def _outcome_from_row(row) -> Outcome:
    """Reconstruye el `Outcome` aparcado. No recalcula nada: el veredicto es el
    que produjo el `OutcomeAdapter` en su momento, tal cual quedo guardado."""
    return Outcome(
        prediction_id=row[1], domain=row[2], subject=row[3],
        status=OutcomeStatus(row[4]), score=float(row[5] or 0.0),
        resolved_ts=float(row[7] or 0.0),
    )


def pending_outcomes(
    *, domain: Optional[str] = None, limit: int = 200,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Resultados verificados que esperan a que exista su estrella."""
    try:
        conn = _open_for_read(db_path)
    except Exception as exc:
        logger.debug("outcome_gravity pending_outcomes no disponible: %s", exc)
        return []
    if conn is None:
        return []
    cols = (*_COLUMNS[:-1], "deferred_at", "last_attempt_at", "attempts")
    where, params = ("WHERE domain = ?", [domain]) if domain else ("", [])
    try:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM pending_outcomes {where} "
            "ORDER BY deferred_at DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    except Exception as exc:
        logger.debug("outcome_gravity pending failed: %s", exc)
        return []
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def pending_count(*, domain: Optional[str] = None,
                  db_path: Optional[str] = None) -> int:
    try:
        conn = _open_for_read(db_path)
    except Exception as exc:
        logger.debug("outcome_gravity pending_count no disponible: %s", exc)
        return 0
    if conn is None:
        return 0
    where, params = ("WHERE domain = ?", [domain]) if domain else ("", [])
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM pending_outcomes {where}", tuple(params),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


# ── Procedencia (lectura) ─────────────────────────────────────────────

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
    try:
        conn = _open_for_read(db_path)
    except Exception as exc:
        logger.debug("outcome_gravity provenance no disponible: %s", exc)
        return []
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
    try:
        conn = _open_for_read(db_path)
    except Exception as exc:
        logger.debug("outcome_gravity applied_count no disponible: %s", exc)
        return 0
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
