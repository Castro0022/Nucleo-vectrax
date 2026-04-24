"""
Registry — builds and wires the complete recovery engine.

Called once from services/core/app.py startup when RESILIENCE_ENABLED=1.

Returns a tuple (orchestrator, executor) that the app can start/stop
and expose via /v1/recovery/status.
"""

from __future__ import annotations

import logging
import os
from typing import Tuple

from core.recovery.executor import RecoveryExecutor
from core.recovery.orchestrator import HealthOrchestrator
from core.recovery.strategies import RecoveryStrategy

logger = logging.getLogger("vectrax.recovery.registry")


def build_engine(dry_run: bool = False) -> Tuple[HealthOrchestrator, RecoveryExecutor]:
    """Construct the orchestrator + executor with all v1 detectors/strategies.

    Args:
      dry_run: if True, the executor runs dry_run() on all actions even
               when in "real" mode. Useful for first-deploy observation.

    Returns:
      (orchestrator, executor) — both not yet started. Caller decides when.
    """
    # === Detectors ===
    from core.recovery.detectors import gateway_silent

    # === Strategies ===
    from core.recovery.strategies import classA_revert_webhook

    # Compose
    env_path = os.environ.get("RECOVERY_ENV_PATH", "/app/.env")
    compose_dir = os.environ.get("RECOVERY_COMPOSE_DIR", "/opt/vectrax")

    strategies = [
        classA_revert_webhook.build(env_path=env_path, compose_dir=compose_dir),
    ]

    executor = RecoveryExecutor(strategies=strategies, dry_run_global=dry_run)

    orchestrator = HealthOrchestrator()
    orchestrator.set_executor(executor.on_event)

    # Register detectors
    orchestrator.register_detector(
        detector_id=gateway_silent.DETECTOR_ID,
        probe=gateway_silent.probe,
        interval_s=int(os.environ.get("RECOVERY_INTERVAL_GATEWAY_SILENT", "60")),
    )

    logger.info(
        "Recovery engine built: %d strategies, %d detectors (dry_run=%s)",
        len(strategies),
        len(orchestrator._runners),
        dry_run,
    )
    return orchestrator, executor
