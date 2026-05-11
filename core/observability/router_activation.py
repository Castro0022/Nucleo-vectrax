"""
core/observability/router_activation.py
=========================================
Logging unificado de activación del router POR MENSAJE.

Diseñado para responder en cualquier momento:
    "¿Qué motores se evaluaron para este mensaje, cuáles se
     activaron, cuáles se omitieron, por qué, cuánto tardó cada uno
     y cuál fue la decisión final del router?"

REGLAS:
    * NO cambia la lógica del router ni del pipeline.
    * NO falla aunque el sink esté roto: try/except en cada hook.
    * NO loggea contenido sensible (el `content` solo se trunca para
      debug si el caller lo pasa explícitamente).
    * Persistencia: JSONL append-only en data/router_activation.jsonl.
      Migrable a SQLite/Postgres más adelante sin tocar callers.

USO desde el pipeline:

    log = open_message_log(message_id, user_id, content=content[:200])
    with track_engine(log, "fast_path") as t:
        ...
        t.record_skip(reason="creator bypass")
    with track_engine(log, "smart_router") as t:
        ...
        t.record_activate(reason=f"strategy={strategy}")
    close_message_log(log, final_source="llm", intent="creator_message")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger("vectrax.observability.router_activation")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class EngineActivation:
    name: str
    activated: bool
    reason: str = ""
    latency_ms: float = 0.0
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouterActivationLog:
    """Snapshot completo de la decisión de routing para UN mensaje."""
    message_id: str
    user_id: str
    timestamp: str  # ISO-8601 UTC
    channel: str = ""
    content_preview: str = ""  # primeros 200 chars, opcional
    intent: str = ""  # intent detectado (smart_route.topic, etc.)
    candidates: List[str] = field(default_factory=list)
    engines: List[EngineActivation] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    final_source: str = ""  # 'memory'|'fast'|'llm'|'places'|'online'|...
    final_decision_reason: str = ""
    total_latency_ms: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sink JSONL append-only
# ---------------------------------------------------------------------------

class JsonlActivationSink:
    """
    Sink simple: una línea JSON por mensaje, append. Threadsafe.
    Defecto: data/router_activation.jsonl.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or os.environ.get(
            "VECTRAX_ROUTER_ACTIVATION_LOG",
            os.path.join("data", "router_activation.jsonl"),
        )
        self._lock = threading.Lock()
        parent = os.path.dirname(self._path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception:
            pass

    @property
    def path(self) -> str:
        return self._path

    def write(self, record: RouterActivationLog) -> None:
        try:
            line = json.dumps(record.to_jsonable(), ensure_ascii=False)
        except Exception as exc:
            logger.debug("activation log serialize failed: %s", exc)
            return
        try:
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:
            logger.debug("activation log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_sink_lock = threading.Lock()
_sink: Optional[JsonlActivationSink] = None


def get_sink() -> JsonlActivationSink:
    global _sink
    with _sink_lock:
        if _sink is None:
            _sink = JsonlActivationSink()
        return _sink


def set_sink_for_tests(sink: Optional[JsonlActivationSink]) -> None:
    """Tests pueden inyectar un sink temporal (ruta tmp)."""
    global _sink
    with _sink_lock:
        _sink = sink


def reset_sink_for_tests() -> None:
    set_sink_for_tests(None)


# ---------------------------------------------------------------------------
# Helpers de uso desde el pipeline
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_message_id() -> str:
    return uuid.uuid4().hex


# Lista canónica de motores candidatos por mensaje. Esto es el
# universo posible, no significa que todos se activen. El caller
# puede sobreescribir el set si lo necesita.
DEFAULT_CANDIDATE_ENGINES: List[str] = [
    "intake_filter",
    "dominant_vector",
    "opportunity_observer",
    "opportunity_suggestions",
    "memory_user",
    "temporal_context",
    "team_memory",
    "lead_activity_handler",
    "fast_path",
    "self_context",
    "nucleus_resolver",
    "smart_router",
    "place_search",
    "online_search",
    "intelligence_bridge",
    "openai_direct",
    "identity_layer",
    "response_consolidator",
    "anti_repetition",
    "voice_synthesizer",
    "law_enforcement",
    "learning_cycle",
]


def open_message_log(
    message_id: Optional[str],
    user_id: str,
    channel: str = "",
    content: str = "",
    candidates: Optional[List[str]] = None,
) -> RouterActivationLog:
    """Inicia un log para un mensaje. Idempotente — no toca disco aún."""
    return RouterActivationLog(
        message_id=message_id or new_message_id(),
        user_id=str(user_id),
        timestamp=_now_iso(),
        channel=channel,
        content_preview=(content or "")[:200],
        candidates=list(candidates or DEFAULT_CANDIDATE_ENGINES),
    )


class _TrackHandle:
    """
    Handle interno devuelto por `track_engine`. Permite al caller
    marcar `record_activate(...)` o `record_skip(...)` dentro del
    bloque. Si no se llama a ninguno, el comportamiento por defecto
    es marcar como skipped con motivo "no decision".
    """

    def __init__(self, log: RouterActivationLog, name: str) -> None:
        self._log = log
        self._name = name
        self._start: float = 0.0
        self._activated: Optional[bool] = None
        self._reason: str = ""
        self._extra: Dict[str, Any] = {}
        self._error: Optional[str] = None

    def record_activate(
        self, reason: str = "", **extra: Any,
    ) -> None:
        self._activated = True
        self._reason = reason
        if extra:
            self._extra.update(extra)

    def record_skip(self, reason: str = "", **extra: Any) -> None:
        self._activated = False
        self._reason = reason
        if extra:
            self._extra.update(extra)

    def record_error(self, error: str) -> None:
        self._error = error[:300]


@contextmanager
def track_engine(log: RouterActivationLog, name: str):
    """
    Context manager para medir latencia y registrar la activación de
    un motor. Robusto: si el bloque lanza, se captura como error y
    se sigue.
    """
    handle = _TrackHandle(log, name)
    handle._start = time.perf_counter()
    try:
        yield handle
    except Exception as exc:
        handle.record_error(f"{type(exc).__name__}: {exc}")
        # Re-raise para no enmascarar bugs reales
        raise
    finally:
        latency_ms = (time.perf_counter() - handle._start) * 1000.0
        # Decisión por defecto si el caller no especificó nada
        if handle._activated is None:
            handle._activated = False
            handle._reason = handle._reason or "no decision recorded"
        try:
            log.engines.append(EngineActivation(
                name=name,
                activated=bool(handle._activated),
                reason=handle._reason,
                latency_ms=round(latency_ms, 3),
                error=handle._error,
                extra=dict(handle._extra),
            ))
            if handle._error:
                log.errors.append({
                    "engine": name,
                    "error": handle._error,
                })
        except Exception as exc:
            logger.debug("track_engine append failed: %s", exc)


def record_skip(
    log: RouterActivationLog, name: str, reason: str = "",
) -> None:
    """Atajo: registrar un motor saltado sin contexto medido."""
    try:
        log.engines.append(EngineActivation(
            name=name,
            activated=False,
            reason=reason,
            latency_ms=0.0,
        ))
    except Exception:
        pass


def record_activate(
    log: RouterActivationLog,
    name: str,
    reason: str = "",
    latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    """Atajo: registrar activación con latencia ya calculada."""
    try:
        log.engines.append(EngineActivation(
            name=name,
            activated=True,
            reason=reason,
            latency_ms=round(float(latency_ms), 3),
            extra=dict(extra),
        ))
    except Exception:
        pass


def record_error(log: RouterActivationLog, engine: str, error: str) -> None:
    try:
        log.errors.append({"engine": engine, "error": error[:300]})
    except Exception:
        pass


def close_message_log(
    log: RouterActivationLog,
    final_source: str = "",
    final_decision_reason: str = "",
    intent: str = "",
) -> None:
    """Marca decisión final y escribe al sink."""
    try:
        log.final_source = final_source or ""
        log.final_decision_reason = final_decision_reason or ""
        if intent:
            log.intent = intent
        # Suma latencias de engines reportadas
        log.total_latency_ms = round(
            sum(e.latency_ms for e in log.engines), 3,
        )
        get_sink().write(log)
    except Exception as exc:
        logger.debug("close_message_log failed: %s", exc)


# ---------------------------------------------------------------------------
# Carga para análisis offline (tests + reportes)
# ---------------------------------------------------------------------------

def iter_records(path: Optional[str] = None):
    """Generator: lee el JSONL y yieldea dicts."""
    p = path or get_sink().path
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue
