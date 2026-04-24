"""
Clase G — Runtime detector: handler hardcoded que bypassea cognición.

Tesis
-----
Un handler que responde al usuario con texto literal (blurb producto, template
fijo, `return "…"`) sin pasar por el pipeline cognitivo (memoria + LLM +
identity_layer) produce respuestas idénticas a todos los usuarios, ignora
contexto, ignora idioma y se replica en múltiples archivos cada vez que
alguien agrega un fast-path.

Este detector observa las respuestas que el sistema envía al usuario y cuenta
repeticiones por hash de texto normalizado. Cuando el mismo hash se repite
por encima del umbral configurado, emite BROKEN con evidencia suficiente
para que la Strategy clase G genere una propuesta de refactor.

Principios de diseño
--------------------
* Observador pasivo. No modifica ni intercepta respuestas. Solo registra.
* Thread-safe. Llamado desde threads del gateway (pool de workers).
* Memoria acotada: LRU + ventana temporal; no crece sin límite.
* Sin PII. Nunca almacena el texto completo de las respuestas; solo
  hash, longitud y prefijo corto anonimizado para diagnóstico.
* Nunca bloquea. Todas las operaciones son O(1) amortizado con locks cortos.

Integración con el motor
------------------------
El orchestrator llama a `probe()` cada `interval_s`. El probe consulta el
estado interno y emite HealthEvent(OK | DEGRADED | BROKEN) según el conteo
actual de hashes sospechosos.

Los gateways llaman a `register_response(user_id, text, lang)` cuando envían
una respuesta al usuario. Ese registro alimenta el contador interno.

Umbrales
--------
* DUPLICATE_THRESHOLD: respuestas idénticas (mismo hash) para disparar
  sospecha. Default 5.
* WINDOW_S: ventana temporal sobre la que se cuentan duplicados. Default
  3600 (1h).
* UNIQUE_USERS_THRESHOLD: al menos N usuarios distintos recibiendo el
  mismo texto antes de reportar. Evita falso positivo cuando un solo
  usuario insiste en hacer la misma pregunta. Default 3.

Estos umbrales son configurables vía env vars:
    RECOVERY_G_DUPLICATE_THRESHOLD, RECOVERY_G_WINDOW_S,
    RECOVERY_G_UNIQUE_USERS_THRESHOLD.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.recovery.events import HealthEvent, HealthState

logger = logging.getLogger("vectrax.recovery.detectors.hardcoded_handler_runtime")

DETECTOR_ID = "hardcoded_handler_runtime"

# Configurable thresholds
DUPLICATE_THRESHOLD = int(os.environ.get("RECOVERY_G_DUPLICATE_THRESHOLD", "5"))
WINDOW_S = int(os.environ.get("RECOVERY_G_WINDOW_S", "3600"))
UNIQUE_USERS_THRESHOLD = int(os.environ.get("RECOVERY_G_UNIQUE_USERS_THRESHOLD", "3"))

# Minimum response length to even consider tracking. Shorter responses
# (e.g. "OK", "Hola") are expected to repeat and are not antipattern.
MIN_TRACKED_LENGTH = 40


@dataclass
class _Occurrence:
    """Single recorded response occurrence."""

    ts: float
    user_id: str
    lang: str


@dataclass
class _HashRecord:
    """All occurrences of a given normalized-text hash, within the window."""

    text_prefix: str  # First 60 chars, anonymized preview for diagnosis
    text_length: int
    occurrences: Deque[_Occurrence] = field(default_factory=deque)

    def active_count(self, now: float, window_s: int) -> int:
        """Prune expired occurrences and return current window count."""
        cutoff = now - window_s
        while self.occurrences and self.occurrences[0].ts < cutoff:
            self.occurrences.popleft()
        return len(self.occurrences)

    def unique_users(self) -> int:
        return len({o.user_id for o in self.occurrences})

    def languages(self) -> List[str]:
        return sorted({o.lang for o in self.occurrences if o.lang})


class _Registry:
    """Thread-safe in-memory registry of response hashes.

    Bounded by MAX_TRACKED_HASHES to prevent unbounded memory growth.
    LRU eviction: oldest-first when at capacity.
    """

    MAX_TRACKED_HASHES = 2048

    def __init__(self) -> None:
        self._hashes: Dict[str, _HashRecord] = {}
        self._order: Deque[str] = deque()  # LRU order
        self._lock = threading.Lock()

    def register(self, text: str, user_id: str, lang: str) -> None:
        """Record a response. Only tracks responses >= MIN_TRACKED_LENGTH."""
        if not text or len(text) < MIN_TRACKED_LENGTH:
            return
        h = self._hash(text)
        now = time.time()
        with self._lock:
            rec = self._hashes.get(h)
            if rec is None:
                # Evict oldest if at capacity
                if len(self._hashes) >= self.MAX_TRACKED_HASHES:
                    oldest = self._order.popleft()
                    self._hashes.pop(oldest, None)
                rec = _HashRecord(
                    text_prefix=self._anonymize_prefix(text),
                    text_length=len(text),
                )
                self._hashes[h] = rec
                self._order.append(h)
            rec.occurrences.append(
                _Occurrence(ts=now, user_id=str(user_id or "anon"), lang=str(lang or ""))
            )

    def snapshot_broken_hashes(
        self,
        duplicate_threshold: int,
        window_s: int,
        unique_users_threshold: int,
    ) -> List[Tuple[str, _HashRecord, int]]:
        """Return list of (hash, record, count) for hashes exceeding thresholds.

        A hash is "broken" when BOTH:
          - active_count in window >= duplicate_threshold
          - unique_users >= unique_users_threshold
        """
        now = time.time()
        result: List[Tuple[str, _HashRecord, int]] = []
        with self._lock:
            for h, rec in self._hashes.items():
                count = rec.active_count(now, window_s)
                if count >= duplicate_threshold and rec.unique_users() >= unique_users_threshold:
                    result.append((h, rec, count))
        return result

    def clear(self) -> None:
        with self._lock:
            self._hashes.clear()
            self._order.clear()

    @staticmethod
    def _hash(text: str) -> str:
        normalized = text.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _anonymize_prefix(text: str) -> str:
        """First 60 chars, single-line, for diagnostic display.

        Not PII-sensitive because this is system-generated response text,
        not user input.
        """
        return " ".join(text.split())[:60]


_REGISTRY = _Registry()


def register_response(user_id: str, text: str, lang: str = "") -> None:
    """Public API for gateways to record a response sent to a user.

    Called from within the gateway's send/response path, AFTER the response
    text has been finalized but BEFORE / independent of actually delivering it.

    This function never raises and never blocks for meaningful time.
    """
    try:
        _REGISTRY.register(text=text, user_id=user_id, lang=lang)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("register_response swallowed: %s", exc)


def probe() -> HealthEvent:
    """Probe function — called by the orchestrator every interval.

    Consults the registry for hashes exceeding thresholds. Emits:
      - OK: no suspicious hashes
      - DEGRADED: suspicious hashes present but below escalation tier
      - BROKEN: at least one hash clearly hit both thresholds (actionable)
    """
    suspects = _REGISTRY.snapshot_broken_hashes(
        duplicate_threshold=DUPLICATE_THRESHOLD,
        window_s=WINDOW_S,
        unique_users_threshold=UNIQUE_USERS_THRESHOLD,
    )

    evidence: Dict[str, Any] = {
        "tracked_hashes_total": len(_REGISTRY._hashes),  # read-only snapshot
        "duplicate_threshold": DUPLICATE_THRESHOLD,
        "window_s": WINDOW_S,
        "unique_users_threshold": UNIQUE_USERS_THRESHOLD,
    }

    if not suspects:
        return HealthEvent(detector_id=DETECTOR_ID, state=HealthState.OK, evidence=evidence)

    # Build structured evidence for strategy consumption. Sort by count desc.
    suspects.sort(key=lambda t: t[2], reverse=True)
    top = suspects[0]
    top_hash, top_rec, top_count = top
    evidence.update(
        {
            "reason": (
                f"Response hash {top_hash} repeated {top_count} times across "
                f"{top_rec.unique_users()} users in the last {WINDOW_S}s — "
                f"indicates a hardcoded handler bypassing cognition."
            ),
            "top_hash": top_hash,
            "top_count": top_count,
            "top_unique_users": top_rec.unique_users(),
            "top_languages": top_rec.languages(),
            "top_text_prefix": top_rec.text_prefix,
            "top_text_length": top_rec.text_length,
            "suspects_total": len(suspects),
        }
    )
    return HealthEvent(detector_id=DETECTOR_ID, state=HealthState.BROKEN, evidence=evidence)


def reset_state_for_tests() -> None:
    """Test helper — clear the registry. Only for unit tests."""
    _REGISTRY.clear()


def _registry_for_tests() -> _Registry:
    """Expose the internal registry for tests that need fine-grained control."""
    return _REGISTRY
