"""
Vectrax ANOMALY_DETECTOR
==========================
Detecta patrones fuera de lo normal en entradas (texto, eventos, datos).
Marca sin concluir — no genera verdad, solo señal.

Principio: la señal NO es la verdad. Este módulo solo levanta banderas
para que el InvestigationEngine las evalúe.

Tipos de anomalía detectados:
  - frequency_spike: aumento súbito en frecuencia de un patrón
  - new_pattern: patrón nunca visto antes
  - distribution_shift: cambio en distribución de categorías
  - repetition_burst: ráfaga de entradas idénticas
  - outlier_input: entrada atípica por longitud o formato

Privacy: solo almacena descriptores abstractos — nunca contenido raw,
datos personales, credenciales o PII.

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Deque, Dict, List, Optional
from statistics import mean, stdev

from core.learning_cycle import (
    ANOMALY_TYPES,
    ANOMALY_WINDOW_SIZE,
    FREQUENCY_SPIKE_FACTOR,
    REPETITION_BURST_THRESHOLD,
)

logger = logging.getLogger("vectrax.learning_cycle.anomaly_detector")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AnomalySignal:
    """Una señal de anomalía — NO es una conclusión, solo una marca."""

    id: str = field(default_factory=lambda: f"ANOM-{hashlib.md5(str(time.time()).encode()).hexdigest()[:8].upper()}")
    anomaly_type: str = "new_pattern"
    severity: str = "low"           # low | medium | high | critical
    description: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    source_fingerprint: str = ""    # hash abstracto del input
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InputEvent:
    """Evento de entrada normalizado para análisis."""

    text: str = ""
    intent: str = ""
    source: str = ""
    length: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Hash abstracto del contenido (no almacena raw)."""
        raw = f"{self.intent}:{self.source}:{self.length}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Anomaly Detector
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    Motor de detección de anomalías.

    Mantiene una ventana deslizante de eventos recientes y compara
    cada nuevo evento contra las estadísticas de la ventana.

    REGLA FUNDAMENTAL: este motor SOLO marca señales.
    Nunca concluye verdad ni genera hipótesis directamente.

    Usage::

        detector = AnomalyDetector()
        signals = detector.analyze(InputEvent(
            text="mensaje del usuario",
            intent="ASK_MEMORY",
            source="telegram",
        ))
        # signals es una lista de AnomalySignal (puede estar vacía)
    """

    def __init__(
        self,
        window_size: int = ANOMALY_WINDOW_SIZE,
        on_anomaly: Optional[Callable[[AnomalySignal], None]] = None,
    ) -> None:
        self._window: Deque[InputEvent] = deque(maxlen=window_size)
        self._intent_history: Deque[str] = deque(maxlen=window_size * 2)
        self._fingerprint_counter: Counter = Counter()
        self._known_intents: set = set()
        self._known_sources: set = set()
        self._length_history: Deque[int] = deque(maxlen=window_size)
        self._on_anomaly = on_anomaly
        self._total_analyzed: int = 0
        self._total_anomalies: int = 0

        logger.info(
            "AnomalyDetector initialized (window=%d)", window_size,
        )

    # =====================================================================
    # Main API
    # =====================================================================

    def analyze(self, event: InputEvent) -> List[AnomalySignal]:
        """
        Analiza un evento de entrada y retorna anomalías detectadas.

        Retorna lista vacía si no hay anomalías.
        NUNCA concluye verdad — solo marca señales.
        """
        signals: List[AnomalySignal] = []

        # --- 1. Frequency spike ---
        spike = self._check_frequency_spike(event)
        if spike:
            signals.append(spike)

        # --- 2. New pattern ---
        new_pat = self._check_new_pattern(event)
        if new_pat:
            signals.append(new_pat)

        # --- 3. Distribution shift ---
        dist_shift = self._check_distribution_shift(event)
        if dist_shift:
            signals.append(dist_shift)

        # --- 4. Repetition burst ---
        rep_burst = self._check_repetition_burst(event)
        if rep_burst:
            signals.append(rep_burst)

        # --- 5. Outlier input ---
        outlier = self._check_outlier_input(event)
        if outlier:
            signals.append(outlier)

        # --- Update window ---
        self._window.append(event)
        self._intent_history.append(event.intent)
        self._fingerprint_counter[event.fingerprint] += 1
        self._known_intents.add(event.intent)
        self._known_sources.add(event.source)
        self._length_history.append(event.length or len(event.text))
        self._total_analyzed += 1

        # --- Notify and count ---
        for signal in signals:
            self._total_anomalies += 1
            if self._on_anomaly:
                try:
                    self._on_anomaly(signal)
                except Exception as exc:
                    logger.warning("on_anomaly callback error: %s", exc)

        if signals:
            logger.info(
                "Anomalies detected: %d from event (intent=%s, source=%s)",
                len(signals), event.intent, event.source,
            )

        return signals

    def feed_batch(self, events: List[InputEvent]) -> List[AnomalySignal]:
        """Analiza un lote de eventos. Retorna todas las anomalías."""
        all_signals: List[AnomalySignal] = []
        for event in events:
            all_signals.extend(self.analyze(event))
        return all_signals

    # =====================================================================
    # Detection strategies (privadas — solo señalan, no concluyen)
    # =====================================================================

    def _check_frequency_spike(self, event: InputEvent) -> Optional[AnomalySignal]:
        """Detecta si el intent actual tiene un spike de frecuencia."""
        if len(self._intent_history) < 10:
            return None

        intent_counts = Counter(self._intent_history)
        total = len(self._intent_history)
        avg_frequency = total / max(len(intent_counts), 1)
        current_count = intent_counts.get(event.intent, 0)

        if current_count > avg_frequency * FREQUENCY_SPIKE_FACTOR:
            severity = "medium" if current_count < avg_frequency * 4 else "high"
            return AnomalySignal(
                anomaly_type="frequency_spike",
                severity=severity,
                description=(
                    f"Intent '{event.intent}' aparece {current_count}x "
                    f"en ventana (promedio={avg_frequency:.1f})"
                ),
                context={
                    "intent": event.intent,
                    "count": current_count,
                    "average": round(avg_frequency, 2),
                    "factor": round(current_count / max(avg_frequency, 0.1), 2),
                },
                source_fingerprint=event.fingerprint,
            )
        return None

    def _check_new_pattern(self, event: InputEvent) -> Optional[AnomalySignal]:
        """Detecta intents o fuentes nunca vistos."""
        if not self._known_intents:
            return None  # Primera vez, no hay referencia

        signals_parts = []

        if event.intent and event.intent not in self._known_intents:
            signals_parts.append(f"intent '{event.intent}' nunca visto")

        if event.source and event.source not in self._known_sources:
            signals_parts.append(f"source '{event.source}' nunca vista")

        if not signals_parts:
            return None

        return AnomalySignal(
            anomaly_type="new_pattern",
            severity="low",
            description="; ".join(signals_parts),
            context={
                "new_intent": event.intent not in self._known_intents,
                "new_source": event.source not in self._known_sources,
                "known_intents_count": len(self._known_intents),
                "known_sources_count": len(self._known_sources),
            },
            source_fingerprint=event.fingerprint,
        )

    def _check_distribution_shift(self, event: InputEvent) -> Optional[AnomalySignal]:
        """Detecta cambio en la distribución de intents."""
        if len(self._intent_history) < 20:
            return None

        history_list = list(self._intent_history)
        half = len(history_list) // 2
        first_half = Counter(history_list[:half])
        second_half = Counter(history_list[half:])

        # Comparar distribuciones: buscar intents que cambiaron >30%
        all_intents = set(first_half.keys()) | set(second_half.keys())
        shifts = []

        for intent in all_intents:
            ratio_first = first_half.get(intent, 0) / max(half, 1)
            ratio_second = second_half.get(intent, 0) / max(len(history_list) - half, 1)
            delta = abs(ratio_second - ratio_first)

            if delta > 0.3:
                shifts.append({
                    "intent": intent,
                    "first_half_ratio": round(ratio_first, 2),
                    "second_half_ratio": round(ratio_second, 2),
                    "delta": round(delta, 2),
                })

        if not shifts:
            return None

        return AnomalySignal(
            anomaly_type="distribution_shift",
            severity="medium",
            description=(
                f"Cambio en distribución de intents: "
                f"{len(shifts)} intent(s) con delta > 30%"
            ),
            context={"shifts": shifts},
            source_fingerprint=event.fingerprint,
        )

    def _check_repetition_burst(self, event: InputEvent) -> Optional[AnomalySignal]:
        """Detecta ráfagas de entradas con el mismo fingerprint."""
        count = self._fingerprint_counter.get(event.fingerprint, 0)

        if count >= REPETITION_BURST_THRESHOLD:
            severity = "medium" if count < 10 else "high"
            return AnomalySignal(
                anomaly_type="repetition_burst",
                severity=severity,
                description=(
                    f"Fingerprint repetido {count}x "
                    f"(umbral={REPETITION_BURST_THRESHOLD})"
                ),
                context={
                    "fingerprint": event.fingerprint,
                    "count": count,
                    "intent": event.intent,
                },
                source_fingerprint=event.fingerprint,
            )
        return None

    def _check_outlier_input(self, event: InputEvent) -> Optional[AnomalySignal]:
        """Detecta entradas atípicas por longitud."""
        input_length = event.length or len(event.text)

        if len(self._length_history) < 10:
            return None

        lengths = list(self._length_history)
        avg_len = mean(lengths)
        std_len = stdev(lengths) if len(lengths) > 1 else 0

        if std_len == 0:
            return None

        z_score = abs(input_length - avg_len) / std_len

        if z_score > 3.0:
            severity = "medium" if z_score < 5.0 else "high"
            return AnomalySignal(
                anomaly_type="outlier_input",
                severity=severity,
                description=(
                    f"Longitud atípica: {input_length} chars "
                    f"(media={avg_len:.0f}, z={z_score:.1f})"
                ),
                context={
                    "length": input_length,
                    "avg_length": round(avg_len, 1),
                    "std_length": round(std_len, 1),
                    "z_score": round(z_score, 2),
                },
                source_fingerprint=event.fingerprint,
            )
        return None

    # =====================================================================
    # Status
    # =====================================================================

    def stats(self) -> Dict[str, Any]:
        """Estadísticas del detector."""
        return {
            "total_analyzed": self._total_analyzed,
            "total_anomalies": self._total_anomalies,
            "window_size": len(self._window),
            "known_intents": len(self._known_intents),
            "known_sources": len(self._known_sources),
            "unique_fingerprints": len(self._fingerprint_counter),
            "anomaly_rate": (
                round(self._total_anomalies / max(self._total_analyzed, 1), 4)
            ),
        }

    def reset(self) -> None:
        """Reset completo (para testing)."""
        self._window.clear()
        self._intent_history.clear()
        self._fingerprint_counter.clear()
        self._known_intents.clear()
        self._known_sources.clear()
        self._length_history.clear()
        self._total_analyzed = 0
        self._total_anomalies = 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_detector: Optional[AnomalyDetector] = None


def get_anomaly_detector() -> AnomalyDetector:
    """Obtener la instancia singleton del AnomalyDetector."""
    global _detector
    if _detector is None:
        _detector = AnomalyDetector()
    return _detector


def reset_anomaly_detector() -> None:
    """Reset para testing. NO usar en producción."""
    global _detector
    _detector = None
