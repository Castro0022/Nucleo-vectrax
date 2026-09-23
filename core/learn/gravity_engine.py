"""
Vectrax Gravity Engine
=======================
Layered memory: HOT → WARM → COLD → DEEP.  Nothing is ever deleted.

Law 1 — Absolute Registration: every event enters the gravity index.
Law 3 — Déjà Vu: reappearing patterns promote to hotter tiers.
Law 4 — No deletion: only cool / compress / archive.

Persistence: ``vault/gravity_index.json``
"""

from __future__ import annotations

import contextlib
import fcntl
import glob
import json
import logging
import os
import statistics
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from core.learn import VAULT_DIR, RUNTIME_DIR
from core.learn.schemas import (
    GravityRecord, Tier, TIER_ORDER, decimate_history, entry_origin_kind,
)

logger = logging.getLogger("vectrax.gravity")

# Persistent path: ~/.vectrax/ (Docker volume, survives deploys)
# Old path: vault/ (bind-mounted code dir, overwritten by rsync)
GRAVITY_INDEX_PATH = os.path.join(RUNTIME_DIR, "gravity_index.json")
_OLD_GRAVITY_PATH = os.path.join(VAULT_DIR, "gravity_index.json")

# Déjà Vu promotion thresholds
DEJAVU_DEEP_TO_COLD_HITS = 1
DEJAVU_COLD_TO_WARM_HITS = 2
DEJAVU_COLD_TO_WARM_WINDOW_DAYS = 14
DEJAVU_WARM_TO_HOT_HITS = 3
DEJAVU_WARM_TO_HOT_WINDOW_DAYS = 7
DEJAVU_WARM_TO_HOT_MIN_CC = 0.6

MAX_OUTCOME_HISTORY = 20

# Bounded size of GravityRecord.activation_history. Calibrated against a
# real sample (Online Retail II, "Year 2009-2010" sheet, ~513k rows /
# 18,370 stars): p95 of hits-per-star = 154, chosen over p99 (483) as the
# more memory-conservative option — 95% of stars in the sample never need
# decimation at this value; only the top ~5% (mostly genuine bestsellers)
# trigger decimate_history's span-preserving thinning. Global (applies to
# every domain, not just sales_trends) — see
# docs/SALES_TRENDS_CALIBRATION_2026_08_25.md for the full methodology,
# distribution, and an unrelated periodicity-detector fix found along the
# way. Revisit if a future domain's real distribution differs materially.
MAX_ACTIVATION_HISTORY = 154


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string, normalizing naive datetimes to UTC-aware.

    Vectrax's own clock (_now_iso) never emits naive timestamps, but
    externally supplied historical data (e.g. a dataset export without a
    UTC offset) can. Without this normalization, a naive value stored via
    replay would later crash any comparison against a real, tz-aware
    ``datetime.now(timezone.utc)`` ("can't compare offset-naive and
    offset-aware datetimes").
    """
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_iso_strict(s: str) -> datetime:
    """Like _parse_iso but raises on invalid input instead of masking it,
    and always returns a UTC-aware datetime (naive input is assumed UTC;
    aware input is converted to UTC).

    Used to validate AND normalize caller-supplied ``event_timestamp``
    before trusting it as the effective clock for replay — an invalid
    string must fall back to ``now``, not silently become "now" disguised
    as a parse success. Normalizing here (the input boundary) guarantees
    every ``effective`` timestamp stored in Gravity (first_seen, last_seen,
    activation_history) is UTC-aware, so it can never later collide with a
    naive datetime during comparison.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Gravity Index
# ---------------------------------------------------------------------------

def _trim_by_origin_kind(entries: List[Any]) -> List[Any]:
    """Acota la historia CONSERVANDO LAS CLASES POR SEPARADO.

    La ventana guarda los `MAX_OUTCOME_HISTORY` mas recientes DE CADA clase de
    procedencia, no los mas recientes en total.

    Recortar el conjunto entero tenia un fallo silencioso: los resultados
    simulados o de procedencia desconocida ocupan plaza en la ventana ANTES de
    que el graduador los excluya, asi que 20 simulados llegados despues de 20
    reales expulsaban a los reales y el patron dejaba de cualificar sin que su
    evidencia real hubiera cambiado. Un simulador podia apagar un criterio
    aprendido de observaciones reales solo por llegar mas tarde.

    Asi, toda procedencia se conserva para auditoria —acotada, tambien la no
    admisible— y la ventana de aprendizaje son los N admisibles mas recientes,
    que es exactamente lo que los umbrales suponen. No cambia ningun umbral:
    corrige sobre que se miden.
    """
    by_kind: Dict[str, List[Any]] = {}
    for entry in entries:
        by_kind.setdefault(entry_origin_kind(entry), []).append(entry)
    kept: List[Any] = []
    for same_kind in by_kind.values():
        same_kind.sort(key=_verified_entry_ts)
        kept.extend(same_kind[-MAX_OUTCOME_HISTORY:])
    kept.sort(key=_verified_entry_ts)
    return kept


def _verified_entry_id(entry: Any) -> str:
    """El `prediction_id` de una entrada de `verified_outcomes`.

    Tolera una entrada en texto plano —la forma que tuvo el campo entre su
    introduccion y la adicion del id— devolviendo "": nunca coincide con un id
    real, asi que una entrada antigua jamas suprime una escritura nueva.
    """
    if isinstance(entry, dict):
        return str(entry.get("id", ""))
    return ""


def _verified_entry_ts(entry: Any) -> float:
    """El instante en que se RESOLVIO ese resultado contra la verdad del
    dominio. Es lo que ordena la ventana; una entrada sin el se trata como la
    mas antigua posible, que es el lugar seguro (nunca desplaza a una mas
    reciente)."""
    if isinstance(entry, dict):
        try:
            return float(entry.get("ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


class GravityIndex:
    """Persisted gravity index — one GravityRecord per fingerprint."""

    def __init__(self, path: str = GRAVITY_INDEX_PATH):
        self.path = path
        self._lock_path = f"{path}.lock"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Auto-migrate from old vault/ path if new path is empty
        self._migrate_from_old_path()
        # Best-effort cleanup of orphaned .tmp.<pid>.<tid> files left behind
        # by a process that was killed/crashed between opening the temp
        # file and the final os.replace() (e.g. OMP abort, OOM kill, macOS
        # sleep/restart). These never touch the real path (see
        # _write_to_disk()) so leaving them is safe but wastes disk
        # indefinitely; removing stray ones from PIDs no longer running is
        # part of "recuperación sin pérdida de registros" hygiene. Never
        # raises, never removes the real path or the lock file.
        self._cleanup_stale_tmp_files()

    def _cleanup_stale_tmp_files(self) -> None:
        try:
            for tmp_path in glob.glob(f"{self.path}.tmp.*"):
                try:
                    parts = os.path.basename(tmp_path).split(".")
                    pid = int(parts[parts.index("tmp") + 1])
                except (ValueError, IndexError):
                    continue
                if pid == os.getpid():
                    continue  # never touch our own in-flight tmp file
                try:
                    os.kill(pid, 0)
                    continue  # process still alive -- might be writing now
                except ProcessLookupError:
                    pass
                except PermissionError:
                    continue  # alive, owned by someone else -- leave it
                try:
                    os.remove(tmp_path)
                    logger.info("Removed orphaned gravity tmp file from dead PID %d: %s", pid, tmp_path)
                except OSError:
                    pass
        except Exception:
            pass

    def _migrate_from_old_path(self) -> None:
        """One-time migration: if new path is empty but old path has data, copy it."""
        if self.path != GRAVITY_INDEX_PATH:
            return  # custom path (tests), skip migration
        try:
            new_size = os.path.getsize(self.path) if os.path.isfile(self.path) else 0
            old_size = os.path.getsize(_OLD_GRAVITY_PATH) if os.path.isfile(_OLD_GRAVITY_PATH) else 0
            if old_size > new_size and old_size > 500:  # old has more data
                import shutil
                shutil.copy2(_OLD_GRAVITY_PATH, self.path)
                logger.info(
                    "MIGRATED gravity_index from vault/ to ~/.vectrax/ (%d bytes)",
                    old_size,
                )
        except Exception:
            pass

    # -- persistence --------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, exclusive: bool = True) -> Iterator[None]:
        """Inter-process (and inter-thread) advisory lock guarding every
        read and write of ``self.path``.

        Corrige la causa estructural del defecto de producción 2026-09-20:
        varios procesos/threads podían intercalar su propio ciclo
        lectura-modificación-escritura sobre ``gravity_index.json`` sin
        ninguna coordinación -- cada `record_event()` leía el archivo
        completo, mutaba SU copia en memoria y volvía a escribir el diccionario
        COMPLETO. Dos llamadas casi simultáneas (dos requests, o un backfill
        masivo con múltiples hilos) podían basarse en el mismo estado leído y
        la segunda escritura pisaba los cambios de la primera ("lost update"),
        además del riesgo de que un lector viera un archivo a medio escribir
        si `os.replace()` llegaba a solaparse con otra escritura en curso.

        `fcntl.flock()` asocia el lock a la DESCRIPCIÓN DE ARCHIVO ABIERTA
        (no al proceso), así que abrir un file descriptor NUEVO en cada
        llamada -- como se hace aquí -- serializa correctamente tanto entre
        PROCESOS distintos como entre HILOS del mismo proceso. Exclusivo
        (`exclusive=True`) para cualquier secuencia que vaya a escribir
        (incluida la lectura previa dentro de esa misma secuencia, ver
        `record_event()`); compartido (`exclusive=False`) para lecturas
        aisladas, que así pueden proceder en paralelo entre sí pero quedan
        bloqueadas mientras un escritor tiene el lock exclusivo.

        Si el proceso que sostiene el lock muere (SIGKILL, OOM, crash) el
        kernel libera el flock automáticamente al cerrar sus file
        descriptors -- nunca queda un lock "huérfano" bloqueando para
        siempre tras un reinicio.
        """
        os.makedirs(os.path.dirname(self._lock_path) or ".", exist_ok=True)
        lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _load(self) -> Dict[str, GravityRecord]:
        """Locked, standalone read (not part of a read-modify-write
        sequence). Internal read-modify-write call sites (``record_event``)
        must NOT call this -- they already hold the exclusive lock for their
        whole critical section and must call ``_read_from_disk()`` directly,
        or the second (shared) lock acquisition attempted here would
        deadlock against the outer exclusive lock held by the same thread.
        """
        with self._locked(exclusive=False):
            return self._read_from_disk()

    def _read_from_disk(self) -> Dict[str, GravityRecord]:
        """Raw disk read -- caller must already hold ``_locked()``."""
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except OSError:
            return {}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            # Producción 2026-09-20: una corrupción localizada (bytes UTF-8
            # inválidos en un puñado de registros, de una escritura
            # concurrente sin lock en _save()) dejaba TODO el índice
            # ilegible -- json.load() nunca llegaba a ejecutarse, así que
            # el except de abajo (que solo captura JSONDecodeError/OSError)
            # nunca actuaba, y `domain_stats()`/dashboard quedaban en blanco
            # sin ningún registro del motivo. errors="replace" permite
            # seguir leyendo TODOS los registros no afectados (Ley 4: nunca
            # borrar) en vez de perder el índice completo por un puñado de
            # bytes dañados; los pocos registros con el byte reemplazado
            # pueden fallar su propio parseo más abajo y se registran igual
            # vía el log, nunca se ocultan en silencio.
            logger.error(
                "gravity_index.json tiene bytes UTF-8 inválidos (%s) -- "
                "leyendo con reemplazo de caracteres para no perder el "
                "índice completo. Revisar/backup: %s", exc, self.path,
            )
            text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "gravity_index.json no parsea como JSON (%s) -- devolviendo "
                "índice vacío para esta lectura, el archivo en disco NO se "
                "modifica. Revisar/backup: %s", exc, self.path,
            )
            return {}
        records: Dict[str, GravityRecord] = {}
        for k, v in data.items():
            try:
                records[k] = GravityRecord.from_dict(v)
            except Exception as exc:
                # Un registro individual corrupto no debe tumbar los otros
                # miles -- se omite ESE registro (se loguea, nunca se
                # inventa) y se preservan todos los demás.
                logger.warning(
                    "gravity_index.json: registro '%s' inválido, omitido "
                    "(%s)", k, exc,
                )
        return records

    def _save(self, records: Dict[str, GravityRecord]) -> None:
        """Locked, standalone write (not part of a read-modify-write
        sequence already holding the lock -- e.g. ``update_records()``).
        """
        with self._locked(exclusive=True):
            self._write_to_disk(records)

    def _write_to_disk(self, records: Dict[str, GravityRecord]) -> None:
        """Atomic, durable write -- caller must already hold ``_locked()``.

        Escritura ATÓMICA (tmp + flush + fsync + os.replace): el gravity
        index se reescribe en cada record_event y es leído concurrentemente
        por census / observer / provider_affinity. tmp+os.replace por sí
        solo ya garantizaba que ningún lector viera un archivo truncado a
        medio escribir; flush()+os.fsync() antes del replace además
        garantizan que el contenido del tmp está físicamente en disco (no
        solo en el buffer del SO) antes de renombrarlo, y el fsync() del
        directorio después del replace garantiza que la propia actualización
        del nombre de archivo sobrevive un crash/corte de energía
        inmediatamente posterior -- sin esto, un reinicio muy cercano al
        replace podía (en teoría, según el filesystem) dejar el directorio
        apuntando todavía al inodo viejo pese a que el rename ya había
        "vuelto" a nivel de aplicación.
        """
        tmp = f"{self.path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {k: v.to_dict() for k, v in records.items()},
                    f, indent=2, ensure_ascii=False,
                )
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            try:
                dir_fd = os.open(os.path.dirname(self.path) or ".", os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass  # best-effort durability extra; the rename already succeeded
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise

    # -- Law 1: absolute registration ---------------------------------------

    def record_event(
        self,
        fingerprint: str,
        cc_score: float = 0.0,
        impact: str = "low",
        domain: str = "unknown",
        intent: str = "",
        outcome: str = "observed",
        summary: str = "",
        event_timestamp: Optional[str] = None,
        **meta: Any,
    ) -> Tuple[GravityRecord, Optional[str]]:
        """
        Register an event.  Returns (record, promotion) where promotion
        is None or the new tier name if Déjà Vu triggered.

        ``event_timestamp`` (optional ISO-8601 string) lets a caller replay
        a historical event with its real occurrence time instead of the
        ingestion time. When omitted, behaviour is identical to before —
        the wall-clock ``now`` is used as the effective clock. When present
        but unparsable, it is ignored and ``now`` is used (never raises).

        Extra metadata kwargs (e.g. ``source="domain_prior"``) are accepted
        and ignored so callers that tag events for provenance — such as
        ``seed_tenant_priors`` — do not raise. ``GravityRecord`` has no slot
        for them; the provenance lives in the fingerprint/summary.

        Concurrencia (corrección 2026-09-20): todo el ciclo lectura-
        modificación-escritura corre bajo un único lock exclusivo (ver
        ``_locked()``) -- dos llamadas concurrentes a ``record_event()``
        (mismo o distinto fingerprint, mismo o distinto proceso) quedan
        totalmente serializadas: la segunda siempre parte del estado que
        dejó la primera, nunca de una copia obsoleta. Usa los helpers SIN
        lock (`_read_from_disk`/`_write_to_disk`) en vez de `_load`/`_save`
        para no intentar adquirir el lock una segunda vez dentro del mismo
        hilo (deadlock).
        """
        with self._locked(exclusive=True):
            return self._record_event_locked(
                fingerprint, cc_score, impact, domain, intent, outcome,
                summary, event_timestamp, **meta,
            )

    def _record_event_locked(
        self,
        fingerprint: str,
        cc_score: float = 0.0,
        impact: str = "low",
        domain: str = "unknown",
        intent: str = "",
        outcome: str = "observed",
        summary: str = "",
        event_timestamp: Optional[str] = None,
        **meta: Any,
    ) -> Tuple[GravityRecord, Optional[str]]:
        """Body of ``record_event()`` -- caller must already hold the
        exclusive lock for the whole read-modify-write sequence."""
        records = self._read_from_disk()
        now = _now_iso()
        effective = self._resolve_effective_timestamp(event_timestamp, now)
        # Parsed once, reused as the single "effective clock" for this call
        # (frequency AND Déjà Vu promotion) so no part of Gravity reasons
        # against the real wall-clock while the record itself lives at a
        # replayed historical instant.
        effective_dt = _parse_iso(effective)
        promotion: Optional[str] = None

        rec = records.get(fingerprint)
        if rec is None:
            rec = GravityRecord(
                fingerprint=fingerprint,
                tier=Tier.HOT.value,
                hits=1,
                first_seen=effective,
                last_seen=effective,
                cc_score=cc_score,
                impact=impact,
                domain=domain,
                intent=intent,
                decay_factor=3.0 if impact == "high" else 1.0,
                summary=summary[:200],
            )
        else:
            rec.hits += 1
            # Replay-safe: first_seen/last_seen always bracket every effective
            # timestamp seen so far, regardless of the order events arrive in
            # (real time is monotonic, so this is a no-op for the default
            # "now" path and only matters for historical backfill).
            if _parse_iso(effective) < _parse_iso(rec.first_seen):
                rec.first_seen = effective
            if _parse_iso(effective) > _parse_iso(rec.last_seen):
                rec.last_seen = effective
            rec.cc_score = cc_score
            rec.impact = impact
            rec.domain = domain
            rec.intent = intent
            if impact == "high":
                rec.decay_factor = 3.0
            if summary:
                rec.summary = summary[:200]

            # Déjà Vu promotion — reasoned against the effective clock, not
            # real wall-clock "now" (see effective_dt comment above).
            promotion = self._check_promotion(rec, effective_dt)

        # Update frequency (measured against the effective clock so historical
        # replay does not compute frequency using the real wall-clock "now").
        first = _parse_iso(rec.first_seen)
        elapsed_days = max((effective_dt - first).total_seconds() / 86400, 0.01)
        rec.freq = round(rec.hits / elapsed_days, 4)

        # Outcome history (keep last N)
        rec.outcome_history.append(outcome)
        if len(rec.outcome_history) > MAX_OUTCOME_HISTORY:
            rec.outcome_history = rec.outcome_history[-MAX_OUTCOME_HISTORY:]

        # Activation history: bounded, span-preserving (see decimate_history).
        rec.activation_history.append(effective)
        rec.activation_history = decimate_history(rec.activation_history, MAX_ACTIVATION_HISTORY)

        records[fingerprint] = rec
        self._write_to_disk(records)
        return rec, promotion

    @staticmethod
    def _resolve_effective_timestamp(event_timestamp: Optional[str], now: str) -> str:
        """Return event_timestamp normalized to a UTC-aware ISO-8601 string,
        or ``now`` if it is missing/unparsable.

        Normalization (not just validation) happens here, at the input
        boundary, so every effective timestamp that reaches first_seen,
        last_seen and activation_history is UTC-aware — regardless of
        whether the caller supplied a naive datetime (e.g. a historical
        dataset export with no UTC offset) or one in another timezone.
        """
        if not event_timestamp:
            return now
        try:
            normalized = _parse_iso_strict(event_timestamp)
            return normalized.isoformat()
        except (ValueError, TypeError):
            return now

    # -- Law 3: Déjà Vu promotion ------------------------------------------

    def _check_promotion(self, rec: GravityRecord, reference_now: datetime) -> Optional[str]:
        """Check if a record qualifies for tier promotion.

        ``reference_now`` is the effective clock for the event just
        recorded (real wall-clock "now" for live events, or the replayed
        historical instant during backfill) — never the real wall-clock
        directly, so a record living in a replayed past does not get its
        Déjà Vu windows evaluated against 2026 while the record itself
        thinks it is 2010. This assumes replay proceeds in non-decreasing
        chronological order; strictly out-of-order backfill can still skew
        window timing (first_seen/last_seen bracketing above remains
        correct regardless).
        """
        tier = Tier(rec.tier)
        now = reference_now
        last = _parse_iso(rec.last_seen)

        if tier == Tier.DEEP:
            if rec.hits >= DEJAVU_DEEP_TO_COLD_HITS:
                rec.tier = Tier.COLD.value
                return Tier.COLD.value

        elif tier == Tier.COLD:
            first = _parse_iso(rec.first_seen)
            # Count recent hits within window
            window = timedelta(days=DEJAVU_COLD_TO_WARM_WINDOW_DAYS)
            if rec.hits >= DEJAVU_COLD_TO_WARM_HITS and (now - last) < window:
                rec.tier = Tier.WARM.value
                return Tier.WARM.value

        elif tier == Tier.WARM:
            window = timedelta(days=DEJAVU_WARM_TO_HOT_WINDOW_DAYS)
            if (rec.hits >= DEJAVU_WARM_TO_HOT_HITS
                    and (now - last) < window
                    and rec.cc_score >= DEJAVU_WARM_TO_HOT_MIN_CC):
                rec.tier = Tier.HOT.value
                return Tier.HOT.value

        return None

    # -- queries ------------------------------------------------------------

    def record_verified_outcome(
        self, fingerprint: str, outcome: str, outcome_id: str, ts: float = 0.0,
        origin_kind: str = "unknown",
    ) -> bool:
        """Anota UN resultado VERIFICADO en la historia graduable de una estrella.

        Devuelve True si el resultado quedo anotado (o ya lo estaba). Ver
        `record_verified_outcomes` para el contrato completo; este es el caso
        de un solo elemento y delega en el mismo cuerpo para que no puedan
        divergir.
        """
        return self.record_verified_outcomes(
            [(fingerprint, outcome, outcome_id, ts, origin_kind)]
        )[0]

    def record_verified_outcomes(
        self, entries: Iterable[Tuple[str, str, str, float, str]],
    ) -> List[bool]:
        """Anota resultados VERIFICADOS en `verified_outcomes`, en UNA sola
        transaccion (un lock, una lectura, una escritura para todo el lote).

        Cada elemento es ``(fingerprint, status, outcome_id, ts, origin_kind)``:
        `ts` es el instante en que ese resultado se RESOLVIO contra la verdad
        del dominio, y `origin_kind` de qué tipo de observacion viene (real,
        simulada o desconocida), que es lo que permite al graduador decidir si
        puede aprender de el. Devuelve una lista de booleanos PARALELA a la entrada: True
        donde la estrella existe y el resultado quedo anotado.

        IDEMPOTENTE POR `outcome_id`
        ----------------------------
        Si la estrella ya tiene una entrada con ese id, NO se anade una segunda
        y se devuelve True igualmente: el resultado ya esta donde tiene que
        estar. Esto cierra la ventana entre esta escritura y la confirmacion
        del registro de procedencia: una caida justo en medio dejaba el
        resultado anotado pero no registrado, y el reintento lo contaba dos
        veces.

        LA VENTANA SON LOS N MAS RECIENTES, NO LOS N ULTIMOS ESCRITOS
        -------------------------------------------------------------
        La comprobacion por id no basta por si sola, y creer que si bastaba fue
        un error de razonamiento: solo puede reconocer lo que TODAVIA esta en
        la ventana. Si entre la caida y el reintento llegan mas de
        `MAX_OUTCOME_HISTORY` resultados nuevos, el id del interrumpido ya fue
        desplazado, el reintento no lo reconoce y lo volvia a insertar COMO
        RECIENTE: un resultado viejo resucitaba, expulsaba a uno genuinamente
        nuevo del final de la ventana y alteraba el criterio.

        Por eso la lista se mantiene ORDENADA por `ts` y se conservan los N
        ultimos. Con ese invariante, reinsertar un resultado ya superado es
        inofensivo: vuelve a caer fuera de la ventana inmediatamente, y nunca
        puede desplazar a uno mas reciente. Ademas coloca en su sitio a un
        resultado recuperado del aparcadero, cuya verdad se resolvio antes de
        los que llegaron mientras esperaba — antes se anotaba como si fuera el
        mas nuevo.

        Es deliberadamente mas estrecho que `record_event()`:

        * Escribe en `verified_outcomes`, NO en `outcome_history`. Esa
          separacion es la correccion de fondo: `outcome_history` la alimenta
          cada ingesta (un ciclo freight de 20 eventos la vacia entera), asi
          que un resultado verificado guardado ahi se perdia antes de poder
          cualificar el patron. Ver el comentario del campo en `schemas.py`.
        * NO incrementa `hits`. `record_event()` si lo hace, y `hits` alimenta
          `combined_hits` de las convergencias: enrutar resultados por ahi
          inflaria la fuerza de la convergencia cada vez que se verifica algo,
          confundiendo "se observo muchas veces" con "se acerto muchas veces".
        * NO toca `cc_score`, `impact`, `freq`, `tier` ni `activation_history`.
          Un resultado dice como salio, no cuanta masa tiene el patron.
        * NO crea la estrella si no existe. Un resultado sin patron al que
          pertenecer no puede inventarse uno: se devuelve False y el llamador
          decide que hacer (ver `core.learn.outcome_gravity`, que lo aparca
          para reintentarlo cuando la estrella exista).

        La cota es `MAX_OUTCOME_HISTORY` (la MISMA que la historia de
        observacion, 20) a proposito: ampliarla ensancharia la ventana sobre la
        que se calculan win_rate y sample_size y facilitaria artificialmente
        que un patron cualifique. Los umbrales (MIN_SAMPLE, MIN_WIN_RATE,
        MIN_EXPECTANCY) y la ventana sobre la que se miden quedan como estaban.
        """
        items = list(entries)
        if not items:
            return []
        results = [False] * len(items)
        with self._locked(exclusive=True):
            records = self._read_from_disk()
            touched = False
            for i, entry in enumerate(items):
                fingerprint, outcome, outcome_id, ts = entry[:4]
                origin_kind = str(entry[4]) if len(entry) > 4 else "unknown"
                rec = records.get(fingerprint)
                if rec is None:
                    continue
                results[i] = True
                if any(_verified_entry_id(e) == outcome_id
                       for e in rec.verified_outcomes):
                    continue  # ya anotado (reintento inmediato tras una caida)
                rec.verified_outcomes.append({
                    "status": str(outcome), "id": str(outcome_id),
                    "ts": float(ts),
                    # QUÉ TIPO de observación es. El graduador decide con esto
                    # si el resultado puede enseñar: ver
                    # `core.gravity_kernel.signals._gradable_history`.
                    "origin_kind": origin_kind,
                })
                # La ventana son los N resultados MAS RECIENTES por el instante
                # en que se resolvieron, no los N ultimos escritos. Ver el
                # razonamiento en el docstring.
                rec.verified_outcomes = _trim_by_origin_kind(
                    rec.verified_outcomes
                )
                records[fingerprint] = rec
                touched = True
            if touched:
                self._write_to_disk(records)
        return results

    def get(self, fingerprint: str) -> Optional[GravityRecord]:
        return self._load().get(fingerprint)

    def get_tier(self, fingerprint: str) -> Optional[str]:
        rec = self.get(fingerprint)
        return rec.tier if rec else None

    def search_by_tier(
        self, tiers: Optional[List[str]] = None
    ) -> Dict[str, List[GravityRecord]]:
        """Group records by tier.  If tiers given, filter to those."""
        records = self._load()
        result: Dict[str, List[GravityRecord]] = {t.value: [] for t in TIER_ORDER}
        for rec in records.values():
            if tiers is None or rec.tier in tiers:
                result.setdefault(rec.tier, []).append(rec)
        return result

    def search_similar(self, fingerprint: str) -> List[GravityRecord]:
        """Find records sharing the same intent prefix (first 6 chars)."""
        prefix = fingerprint[:6]
        return [
            r for r in self._load().values()
            if r.fingerprint[:6] == prefix
        ]

    def tier_counts(self, records: Optional[Dict[str, GravityRecord]] = None) -> Dict[str, int]:
        """``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring for the full rationale).
        """
        counts = {t.value: 0 for t in TIER_ORDER}
        for rec in (records if records is not None else self._load()).values():
            counts[rec.tier] = counts.get(rec.tier, 0) + 1
        return counts

    def tier_stats(self) -> Dict[str, Any]:
        """Summary stats per tier: count, avg_cc, avg_freq."""
        by_tier = self.search_by_tier()
        stats = {}
        for tier_name, recs in by_tier.items():
            if not recs:
                stats[tier_name] = {"count": 0, "avg_cc": 0.0, "avg_freq": 0.0}
                continue
            cc_vals = [r.cc_score for r in recs]
            freq_vals = [r.freq for r in recs]
            stats[tier_name] = {
                "count": len(recs),
                "avg_cc": round(statistics.mean(cc_vals), 4),
                "avg_freq": round(statistics.mean(freq_vals), 4),
            }
        return stats

    def all_records(self) -> List[GravityRecord]:
        return list(self._load().values())

    def update_records(self, records: Dict[str, GravityRecord]) -> None:
        """Bulk update (used by decay engine). Locked write -- see ``_save()``."""
        self._save(records)

    def load_raw(self) -> Dict[str, GravityRecord]:
        """Expose raw load for decay/constellation."""
        return self._load()


    # -- cross-domain queries ------------------------------------------------

    def by_domain(
        self, domain: str, records: Optional[Dict[str, GravityRecord]] = None,
    ) -> List[GravityRecord]:
        """Return all records in a given domain.

        ``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring for the full rationale).
        """
        return [
            r for r in (records if records is not None else self._load()).values()
            if r.domain == domain
        ]

    def domain_stats(self, records: Optional[Dict[str, GravityRecord]] = None) -> Dict[str, Dict[str, Any]]:
        """Aggregate stats per domain (market, cognition, etc.).

        ``records``, if provided, is used instead of re-reading the index
        from disk — lets a caller that already loaded the full index once
        (e.g. alongside ``all_records()``) avoid paying the disk read +
        JSON parse cost again for a large index. Defaults to ``self._load()``
        for full backward compatibility.
        """
        domains: Dict[str, list] = {}
        for rec in (records if records is not None else self._load()).values():
            domains.setdefault(rec.domain, []).append(rec)
        result = {}
        for d, recs in domains.items():
            cc_vals = [r.cc_score for r in recs]
            freq_vals = [r.freq for r in recs]
            tiers = {}
            for r in recs:
                tiers[r.tier] = tiers.get(r.tier, 0) + 1
            result[d] = {
                "count": len(recs),
                "avg_cc": round(statistics.mean(cc_vals), 4) if cc_vals else 0,
                "avg_freq": round(statistics.mean(freq_vals), 4) if freq_vals else 0,
                "total_hits": sum(r.hits for r in recs),
                "tiers": tiers,
            }
        return result

    # Domain groups larger than this are sampled to their top-K members by
    # gravitational weight (hits × cc × freq × decay) for the *global*
    # cross-domain scan only. Explicit two-domain calls (domain_a given)
    # remain exact/uncapped. Without this, a naive all-domain-pairs scan
    # would be O(N²) across every star in the index (e.g. sales_trends
    # alone reached 28,435 records after its 2026-08-26 backfill) — this
    # cap keeps the global scan bounded while a real caller can still ask
    # for an exact two-domain comparison. This is an explicit, documented
    # tradeoff, not a silent limitation, and is the seam future work should
    # replace with real constellation-level comparison (grouping domains
    # into semantic clusters before pairing) instead of raising the cap.
    MAX_DOMAIN_GROUP_FOR_GLOBAL_SCAN = 300

    # HOTFIX (2026-08-27, production incident): bulk-ingested domains (e.g.
    # florida_real_estate, freight_logistics) routinely score cc_score in the
    # 0.7-1.0 range and have many records with recent last_seen timestamps —
    # no cc/size threshold reliably separates real temporal_proximity signal
    # from this noise. Combined with autonomous_observer.py calling the
    # GLOBAL scan every ~2-3s (via get_full_convergence_candidates()), this
    # produced tens of thousands of spurious temporal_proximity convergences
    # per hour in the canonical registry. Disabling temporal_proximity for
    # the automatic global scan only; explicit two-domain queries (domain_a
    # given) are exact/targeted and keep it enabled. See convergence_registry
    # for the canonical registry this feeds.
    ENABLE_TEMPORAL_PROXIMITY_IN_GLOBAL_SCAN = False

    def _weight(self, r: GravityRecord) -> float:
        return r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor

    def _match_pair(
        self, a: GravityRecord, b: GravityRecord, domain_a: str, domain_b: str,
        min_cc: float, include_temporal_proximity: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Shared pairwise matching logic (unchanged heuristic, factored out
        so both the explicit two-domain path and the global multi-pair path
        reuse the exact same rules).

        ``include_temporal_proximity``: see ENABLE_TEMPORAL_PROXIMITY_IN_GLOBAL_SCAN
        above — the global scan passes this as False; explicit two-domain
        callers keep the original exact behaviour (True).
        """
        if a.intent and b.intent and (
            a.intent.lower() in b.intent.lower()
            or b.intent.lower() in a.intent.lower()
        ):
            combined_cc = (a.cc_score + b.cc_score) / 2
            if combined_cc >= min_cc:
                return {
                    "type": "intent_overlap",
                    "star_a": a.fingerprint,
                    "star_b": b.fingerprint,
                    "intent": a.intent,
                    "combined_cc": round(combined_cc, 4),
                    "combined_hits": a.hits + b.hits,
                    "domains": [domain_a, domain_b],
                }
            return None

        if not include_temporal_proximity:
            return None

        a_last = _parse_iso(a.last_seen)
        b_last = _parse_iso(b.last_seen)
        now = datetime.now(timezone.utc)
        both_recent = (
            (now - a_last).total_seconds() < 7 * 86400
            and (now - b_last).total_seconds() < 7 * 86400
        )
        combined_cc_tp = (a.cc_score + b.cc_score) / 2
        if both_recent and combined_cc_tp >= 0.3:
            return {
                "type": "temporal_proximity",
                "star_a": a.fingerprint,
                "star_b": b.fingerprint,
                "a_intent": a.intent,
                "b_intent": b.intent,
                "combined_cc": round(combined_cc_tp, 4),
                "domains": [domain_a, domain_b],
            }
        return None

    def cross_domain_convergences(
        self,
        domain_a: Optional[str] = None,
        domain_b: Optional[str] = None,
        min_cc: float = 0.0,
        records: Optional[Dict[str, GravityRecord]] = None,
    ) -> List[Dict[str, Any]]:
        """Find convergences between domains.

        A convergence is detected when stars from different domains
        share the same intent or have overlapping temporal activity.

        - ``domain_a`` given, ``domain_b`` empty: exact, uncapped match of
          ``domain_a`` against every other domain (today's original
          behaviour, preserved for targeted queries like market↔freight).
        - ``domain_a`` and ``domain_b`` both given: exact, uncapped match
          between exactly those two domains.
        - Neither given (the default): a real GLOBAL scan across every
          domain pair in the index — no domain is hardcoded as the anchor.
          Domain groups above ``MAX_DOMAIN_GROUP_FOR_GLOBAL_SCAN`` are
          sampled to their top-K by gravitational weight for this scan only
          (see the constant's docstring above).

        ``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring — same rationale: avoid
        redundant full-index reloads when a caller already has one).
        """
        records = records if records is not None else self._load()

        if domain_a is not None:
            a_recs = [r for r in records.values() if r.domain == domain_a]
            b_recs = [
                r for r in records.values()
                if (r.domain == domain_b if domain_b else r.domain != domain_a)
            ]
            convergences = []
            for a in a_recs:
                for b in b_recs:
                    match = self._match_pair(
                        a, b, domain_a, domain_b or "*", min_cc,
                        include_temporal_proximity=True,
                    )
                    if match:
                        convergences.append(match)
            return convergences

        # Global mode: group by domain once (O(N)), then match every
        # unordered domain pair — the whole universe, not just market.
        by_domain: Dict[str, List[GravityRecord]] = {}
        for r in records.values():
            by_domain.setdefault(r.domain, []).append(r)

        capped: Dict[str, List[GravityRecord]] = {}
        for domain, recs in by_domain.items():
            if len(recs) > self.MAX_DOMAIN_GROUP_FOR_GLOBAL_SCAN:
                recs = sorted(recs, key=self._weight, reverse=True)[
                    : self.MAX_DOMAIN_GROUP_FOR_GLOBAL_SCAN
                ]
            capped[domain] = recs

        domains = sorted(capped)
        convergences = []
        _include_tp = self.ENABLE_TEMPORAL_PROXIMITY_IN_GLOBAL_SCAN
        for i, dom_a in enumerate(domains):
            for dom_b in domains[i + 1:]:
                for a in capped[dom_a]:
                    for b in capped[dom_b]:
                        match = self._match_pair(
                            a, b, dom_a, dom_b, min_cc,
                            include_temporal_proximity=_include_tp,
                        )
                        if match:
                            convergences.append(match)
        return convergences

    def universe_summary(self) -> Dict[str, Any]:
        """Full universe snapshot: domains, tiers, convergences."""
        records = self.all_records()
        domains = self.domain_stats()
        tiers = self.tier_counts()
        # No domain arg = global scan across every domain pair (see
        # cross_domain_convergences docstring), not just market vs rest.
        convergences = self.cross_domain_convergences()
        return {
            "total_stars": len(records),
            "tiers": tiers,
            "domains": domains,
            "cross_domain_convergences": len(convergences),
            "convergence_details": convergences[:10],
        }


    def top_stars(
        self,
        n: int = 10,
        domain: Optional[str] = None,
        records: Optional[Dict[str, GravityRecord]] = None,
    ) -> List[GravityRecord]:
        """Return the top N stars by gravitational weight (hits × cc × freq).

        ``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring for the full rationale).
        """
        source = records if records is not None else self._load()
        recs = [r for r in source.values() if r.domain == domain] if domain else list(source.values())
        for r in recs:
            r._weight = r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor
        return sorted(recs, key=lambda r: r._weight, reverse=True)[:n]

    def growth_trends(
        self, days: int = 7, records: Optional[Dict[str, GravityRecord]] = None,
    ) -> Dict[str, Any]:
        """Analyze growth trends over a time window.

        ``records``, if provided, is used instead of re-reading the index
        from disk (see ``domain_stats`` docstring for the full rationale) -
        lets a caller compute multiple windows (e.g. 24h + 7d) from one load.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = records if records is not None else self._load()
        new_stars = [r for r in records.values()
                     if _parse_iso(r.first_seen) >= cutoff]
        active_stars = [r for r in records.values()
                        if _parse_iso(r.last_seen) >= cutoff]
        growing = [r for r in active_stars if r.hits >= 3]

        # Domain breakdown of new stars
        new_by_domain: Dict[str, int] = {}
        for r in new_stars:
            new_by_domain[r.domain] = new_by_domain.get(r.domain, 0) + 1

        return {
            "window_days": days,
            "new_stars": len(new_stars),
            "active_stars": len(active_stars),
            "growing_stars": len(growing),
            "total_stars": len(records),
            "new_by_domain": new_by_domain,
            "top_growing": sorted(
                growing, key=lambda r: r.freq, reverse=True,
            )[:5],
        }


# ---------------------------------------------------------------------------
# Convergence alerts
# ---------------------------------------------------------------------------

# Thresholds for triggering alerts
ALERT_MIN_HITS = 5        # combined hits to consider significant
ALERT_MIN_CC = 0.4        # combined coherence to consider strong
ALERT_GROWTH_FACTOR = 2   # hits doubled since last check

_ALERTS_SENT_FILE = os.path.join(RUNTIME_DIR, "convergence_alerts_sent.json")


def _load_sent_alerts() -> Dict[str, Any]:
    try:
        if os.path.isfile(_ALERTS_SENT_FILE):
            with open(_ALERTS_SENT_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_sent_alerts(sent: Dict[str, Any]) -> None:
    try:
        with open(_ALERTS_SENT_FILE, "w") as f:
            json.dump(sent, f, indent=2)
    except Exception:
        pass


def check_convergence_alerts() -> List[Dict[str, Any]]:
    """Scan for convergences that exceed critical thresholds.

    Returns list of new alerts (not previously sent).
    Each alert includes the convergence detail + reason.
    """
    gi = get_gravity_index()
    convergences = gi.cross_domain_convergences()
    sent = _load_sent_alerts()
    new_alerts = []

    for conv in convergences:
        # Build a stable key for deduplication
        key = f"{conv['star_a']}:{conv['star_b']}"
        prev = sent.get(key, {})
        prev_hits = prev.get("hits", 0)

        hits = conv.get("combined_hits", 0)
        cc = conv.get("combined_cc", 0)
        reasons = []

        # Check thresholds
        if hits >= ALERT_MIN_HITS and prev_hits < ALERT_MIN_HITS:
            reasons.append(f"hits={hits} superó umbral ({ALERT_MIN_HITS})")

        if cc >= ALERT_MIN_CC and prev.get("cc", 0) < ALERT_MIN_CC:
            reasons.append(f"coherencia={cc:.2f} superó umbral ({ALERT_MIN_CC})")

        if prev_hits > 0 and hits >= prev_hits * ALERT_GROWTH_FACTOR:
            reasons.append(f"hits x{hits / max(prev_hits, 1):.1f} desde última alerta")

        if reasons:
            alert = {
                **conv,
                "alert_reasons": reasons,
                "key": key,
            }
            new_alerts.append(alert)
            sent[key] = {"hits": hits, "cc": cc, "alerted_at": time.time()}

    if new_alerts:
        _save_sent_alerts(sent)

    return new_alerts


_ALERT_HISTORY_FILE = os.path.join(RUNTIME_DIR, "convergence_alert_history.jsonl")
_MAX_ALERT_HISTORY = 200


def _append_alert_history(alert: Dict[str, Any]) -> None:
    """Append alert to persistent JSONL history."""
    hist_file = _ALERT_HISTORY_FILE
    try:
        os.makedirs(os.path.dirname(hist_file), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
            "star_a": alert.get("star_a", ""),
            "star_b": alert.get("star_b", ""),
            "intent": alert.get("intent", alert.get("a_intent", "")),
            "type": alert.get("type", ""),
            "combined_hits": alert.get("combined_hits", 0),
            "combined_cc": alert.get("combined_cc", 0),
            "domains": alert.get("domains", []),
            "reasons": alert.get("alert_reasons", []),
        }
        with open(hist_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trim_alert_history() -> None:
    try:
        with open(_ALERT_HISTORY_FILE) as f:
            lines = f.readlines()
        if len(lines) > _MAX_ALERT_HISTORY:
            with open(_ALERT_HISTORY_FILE, "w") as f:
                f.writelines(lines[-_MAX_ALERT_HISTORY:])
    except Exception:
        pass


def get_alert_history(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent alert history (newest first)."""
    hist_file = _ALERT_HISTORY_FILE
    if not os.path.isfile(hist_file):
        return []
    entries = []
    try:
        with open(hist_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return list(reversed(entries[-limit:]))


def format_alert_history(limit: int = 10) -> str:
    """Format alert history for Telegram."""
    alerts = get_alert_history(limit)
    if not alerts:
        return "Sin alertas de convergencia registradas."

    lines = [f"\u26a1 <b>Historial de alertas ({len(alerts)} recientes)</b>\n"]
    for a in alerts:
        ts = a.get("timestamp", "")[:16]
        intent = a.get("intent", "?")
        hits = a.get("combined_hits", 0)
        cc = a.get("combined_cc", 0)
        reasons = ", ".join(a.get("reasons", []))
        lines.append(
            f"[{ts}] <b>{intent}</b>\n"
            f"  {a.get('star_a', '')} \u2194 {a.get('star_b', '')}\n"
            f"  hits={hits} cc={cc} | {reasons}"
        )

    return "\n".join(lines)


def send_convergence_alerts() -> int:
    """Check for critical convergences and record in history.

    DISABLED: Telegram notifications turned off by creator request.
    Convergence data is still recorded in alert_history for analysis
    and available via /vx market alerts. No messages sent to chat.

    Returns number of alerts recorded (not sent).
    """
    alerts = check_convergence_alerts()
    if not alerts:
        return 0

    # Record in history file (available via get_alert_history())
    for alert in alerts:
        _append_alert_history(alert)

    # NO Telegram notification — alerts are silent by default.
    # Data is preserved in vault/convergence_alert_history.jsonl.
    # Creator can check via /vx market alerts when needed.

    return 0  # 0 = no messages sent to Telegram


def universe_report(lang: str = "es") -> str:
    """Generate a human-readable universe report for Telegram / self-context."""
    gi = get_gravity_index()
    summary = gi.universe_summary()
    trends = gi.growth_trends(days=7)
    top = gi.top_stars(n=8)
    convergences = summary.get("convergence_details", [])

    lines = []
    if lang == "es":
        lines.append("🌌 <b>Universo Gravitacional — Reporte</b>")
    else:
        lines.append("🌌 <b>Gravitational Universe — Report</b>")

    # Overview
    total = summary["total_stars"]
    tiers = summary["tiers"]
    lines.append(f"")
    lines.append(f"⭐ {total} estrellas totales")
    lines.append(
        f"   HOT={tiers.get('HOT', 0)} | WARM={tiers.get('WARM', 0)} | "
        f"COLD={tiers.get('COLD', 0)} | DEEP={tiers.get('DEEP', 0)}"
    )

    # Domains
    lines.append("")
    lines.append("<b>Dominios:</b>")
    for d, stats in summary["domains"].items():
        lines.append(
            f"  {d}: {stats['count']} ⭐ | cc={stats['avg_cc']} | "
            f"hits={stats['total_hits']}"
        )

    # Top stars by gravitational weight
    lines.append("")
    lines.append("<b>Estrellas con mayor masa gravitacional:</b>")
    for i, r in enumerate(top, 1):
        weight = round(r.hits * max(r.cc_score, 0.01) * max(r.freq, 0.01) * r.decay_factor, 2)
        lines.append(
            f"  {i}. {r.fingerprint[:25]} [{r.domain}]\n"
            f"     hits={r.hits} cc={r.cc_score:.2f} freq={r.freq:.2f} "
            f"peso={weight}"
        )

    # Growth trends
    lines.append("")
    lines.append(f"<b>Tendencia (últimos {trends['window_days']}d):</b>")
    lines.append(
        f"  Nuevas: {trends['new_stars']} | "
        f"Activas: {trends['active_stars']} | "
        f"Creciendo: {trends['growing_stars']}"
    )
    if trends["new_by_domain"]:
        parts = [f"{d}={n}" for d, n in trends["new_by_domain"].items()]
        lines.append(f"  Nuevas por dominio: {', '.join(parts)}")

    if trends["top_growing"]:
        lines.append("  Más activas:")
        for r in trends["top_growing"]:
            lines.append(
                f"    📈 {r.fingerprint[:25]} freq={r.freq:.2f} hits={r.hits}"
            )

    # Cross-domain convergences
    intent_convs = [c for c in convergences if c["type"] == "intent_overlap"]
    if intent_convs:
        lines.append("")
        lines.append(f"<b>Convergencias cross-domain: {len(intent_convs)}</b>")
        for c in intent_convs:
            lines.append(
                f"  🔗 {c['star_a']} ↔ {c['star_b']}\n"
                f"     symbol={c['intent']} cc={c['combined_cc']} "
                f"hits={c['combined_hits']}"
            )
    else:
        lines.append("")
        lines.append("Convergencias cross-domain: 0 (se forman con observación continua)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_index: Optional[GravityIndex] = None


def get_gravity_index() -> GravityIndex:
    global _index
    if _index is None:
        _index = GravityIndex()
    return _index
