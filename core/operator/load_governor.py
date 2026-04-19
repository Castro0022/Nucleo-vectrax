"""
Vectrax Load Governor — Presión, Capacidad y Estabilización
==============================================================
Módulo autónomo que detecta presión en el sistema y actúa para
protegerlo sin intervención humana.

Niveles de presión:
  GREEN  — todo normal, acepta todo
  YELLOW — presión media, cleanup agresivo, duplicados estrictos
  ORANGE — presión alta, solo fast-path + cola mínima, alerta creador
  RED    — crítico, rechaza todo excepto identidad/memoria

Transiciones:
  GREEN → YELLOW: queue_pending > 10 OR latencia > 8s
  YELLOW → ORANGE: queue_pending > 25 OR latencia > 12s OR worker muerto
  ORANGE → RED: queue_pending > 40 OR latencia > 20s
  RED → ORANGE: queue_pending < 20 AND worker alive
  ORANGE → YELLOW: queue_pending < 10 AND latencia < 8s
  YELLOW → GREEN: queue_pending < 5 AND latencia < 5s

Acciones automáticas:
  YELLOW: cleanup de mensajes viejos, duplicate window más estricto
  ORANGE: solo fast-path acepta, heavy jobs con prioridad baja, alerta Telegram
  RED: rechaza todo excepto fast-path de identidad, alerta urgente

Principio: mejor degradar servicio que colapsar. Ley 5 (Ritmo).

Creado: 2026-03-28
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.load_governor")


# ---------------------------------------------------------------------------
# Pressure levels
# ---------------------------------------------------------------------------

class PressureLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Umbrales ajustados para sistema con LLM (latencias de 3-10s son normales)
THRESHOLDS = {
    # GREEN → YELLOW
    "yellow_queue": 15,
    "yellow_latency": 15.0,
    # YELLOW → ORANGE
    "orange_queue": 35,
    "orange_latency": 25.0,
    # ORANGE → RED
    "red_queue": 50,
    "red_latency": 40.0,
    # Recovery (hysteresis)
    "green_queue": 8,
    "green_latency": 10.0,
    "yellow_recover_queue": 15,
    "yellow_recover_latency": 15.0,
    "orange_recover_queue": 30,
}

# Minimum time between level changes (prevents oscillation)
MIN_TRANSITION_INTERVAL = 30  # seconds


# ---------------------------------------------------------------------------
# Load Governor state
# ---------------------------------------------------------------------------

class LoadGovernor:
    """
    Governor de carga que ajusta el comportamiento del sistema
    según la presión detectada.

    Singleton — una sola instancia controla todo el sistema.
    """

    def __init__(self) -> None:
        self._level = PressureLevel.GREEN
        self._last_transition: float = 0.0
        self._last_alert: float = 0.0
        self._alert_interval = 120  # min seconds between Telegram alerts
        logger.info("LoadGovernor initialized (level=GREEN)")

    @property
    def level(self) -> PressureLevel:
        return self._level

    # =====================================================================
    # Core: evaluate pressure and transition
    # =====================================================================

    def evaluate(self) -> PressureLevel:
        """
        Evalúa el estado del sistema y ajusta el nivel de presión.
        Llamar periódicamente (cada 15s desde supervisor o gateway).
        """
        from core.operator.system_monitor import collect_metrics

        m = collect_metrics()
        now = time.time()
        old_level = self._level

        # Anti-oscillation: no cambiar nivel si cambió hace menos de 30s
        if now - self._last_transition < MIN_TRANSITION_INTERVAL:
            return self._level

        # --- Evaluate escalation (subir presión) ---
        if self._level == PressureLevel.GREEN:
            if (m.queue_pending > THRESHOLDS["yellow_queue"]
                    or m.avg_latency_s > THRESHOLDS["yellow_latency"]):
                self._transition(PressureLevel.YELLOW, m, now)

        elif self._level == PressureLevel.YELLOW:
            if (m.queue_pending > THRESHOLDS["orange_queue"]
                    or m.avg_latency_s > THRESHOLDS["orange_latency"]
                    or not m.worker_alive):
                self._transition(PressureLevel.ORANGE, m, now)

        elif self._level == PressureLevel.ORANGE:
            if (m.queue_pending > THRESHOLDS["red_queue"]
                    or m.avg_latency_s > THRESHOLDS["red_latency"]):
                self._transition(PressureLevel.RED, m, now)

        # --- Evaluate recovery (bajar presión) ---
        if self._level == PressureLevel.RED:
            if (m.queue_pending < THRESHOLDS["orange_recover_queue"]
                    and m.worker_alive):
                self._transition(PressureLevel.ORANGE, m, now)

        elif self._level == PressureLevel.ORANGE:
            if (m.queue_pending < THRESHOLDS["yellow_recover_queue"]
                    and m.avg_latency_s < THRESHOLDS["yellow_recover_latency"]
                    and m.worker_alive):
                self._transition(PressureLevel.YELLOW, m, now)

        elif self._level == PressureLevel.YELLOW:
            if (m.queue_pending < THRESHOLDS["green_queue"]
                    and m.avg_latency_s < THRESHOLDS["green_latency"]):
                self._transition(PressureLevel.GREEN, m, now)

        # --- Execute actions for current level ---
        self._execute_actions(m)

        return self._level

    def _transition(self, new_level: PressureLevel, m, now: float) -> None:
        old = self._level
        self._level = new_level
        self._last_transition = now

        logger.warning(
            "LOAD GOVERNOR: %s → %s (queue=%d, latency=%.1fs, worker=%s)",
            old.value, new_level.value,
            m.queue_pending, m.avg_latency_s,
            "alive" if m.worker_alive else "DEAD",
        )

        # Record in ledger
        try:
            from core.operator import ledger_bridge as ledger
            ledger.record_event(
                action=f"load_governor:{new_level.value}",
                category=ledger.EventCategory.ACTION,
                risk_zone=(
                    ledger.RiskZone.GREEN if new_level == PressureLevel.GREEN
                    else ledger.RiskZone.YELLOW if new_level == PressureLevel.YELLOW
                    else ledger.RiskZone.RED
                ),
                reason=f"Pressure {old.value} → {new_level.value}",
                details={
                    "queue_pending": m.queue_pending,
                    "avg_latency": m.avg_latency_s,
                    "worker_alive": m.worker_alive,
                    "memory_mb": m.memory_mb,
                },
            )
        except Exception:
            pass

    # =====================================================================
    # Actions per level
    # =====================================================================

    def _execute_actions(self, m) -> None:
        """Execute protective actions based on current level."""

        if self._level == PressureLevel.YELLOW:
            self._action_cleanup_queue()

        elif self._level == PressureLevel.ORANGE:
            self._action_cleanup_queue()
            self._action_alert_creator("ORANGE", m)

        elif self._level == PressureLevel.RED:
            self._action_cleanup_queue()
            self._action_purge_old_pending()
            self._action_alert_creator("RED", m)

    def _action_cleanup_queue(self) -> None:
        """Remove old done/error messages from queue."""
        try:
            from core.transport.message_queue import cleanup
            cleanup()
        except Exception:
            pass

    def _action_purge_old_pending(self) -> None:
        """In RED: purge pending messages older than 60s (they'll timeout anyway)."""
        try:
            import sqlite3
            db = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "vault", "message_queue.db",
            )
            conn = sqlite3.connect(db, timeout=2)
            cutoff = time.time() - 60
            purged = conn.execute(
                "DELETE FROM queue WHERE status = 'pending' AND created_at < ?",
                (cutoff,),
            ).rowcount
            conn.commit()
            conn.close()
            if purged:
                logger.warning("RED: purged %d stale pending messages", purged)
        except Exception:
            pass

    def _action_alert_creator(self, level: str, m) -> None:
        """Send alert to creator via Telegram (rate-limited)."""
        now = time.time()
        if now - self._last_alert < self._alert_interval:
            return

        try:
            import httpx
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CREATOR_CHAT_ID", "")
            if not token or not chat_id:
                return

            icon = "🟠" if level == "ORANGE" else "🔴"
            msg = (
                f"{icon} VECTRAX LOAD: {level}\n"
                f"\n"
                f"Queue: {m.queue_pending} pending\n"
                f"Latencia: {m.avg_latency_s}s\n"
                f"Worker: {'ALIVE' if m.worker_alive else 'DEAD'}\n"
                f"RAM: {m.memory_mb} MB\n"
                f"Usuarios activos: {m.active_users}"
            )

            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=5,
            )
            self._last_alert = now
            logger.info("Alert sent to creator: %s", level)
        except Exception:
            pass

    # =====================================================================
    # Query interface (for gateway to check)
    # =====================================================================

    def should_accept_heavy_job(self) -> bool:
        """¿El sistema puede aceptar un job pesado (pipeline)?"""
        return self._level in (PressureLevel.GREEN, PressureLevel.YELLOW)

    def should_accept_any_job(self) -> bool:
        """¿El sistema puede aceptar cualquier job?"""
        return self._level != PressureLevel.RED

    def status(self) -> Dict[str, Any]:
        return {
            "level": self._level.value,
            "last_transition": self._last_transition,
            "accepts_heavy": self.should_accept_heavy_job(),
            "accepts_any": self.should_accept_any_job(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_governor: Optional[LoadGovernor] = None


def get_load_governor() -> LoadGovernor:
    global _governor
    if _governor is None:
        _governor = LoadGovernor()
    return _governor
