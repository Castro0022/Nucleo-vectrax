#!/usr/bin/env python3
"""
Recovery engine simulation tool.

Usage:
  python3 scripts/recovery_simulate.py probe-gateway-silent
  python3 scripts/recovery_simulate.py dry-run-classA
  python3 scripts/recovery_simulate.py dry-run-classA --force-broken

Commands:
  probe-gateway-silent      Run the gateway_silent probe ONCE and print
                            the HealthEvent it would emit.  No strategy
                            is triggered.  Safe on any environment.

  dry-run-classA            Simulate a BROKEN event matching Class A
                            and run the strategy in dry-run mode.  No
                            side effects.  Prints which actions would
                            run in what order.

  dry-run-classA --force-broken
                            Like above, but bypasses the 3-confirmation
                            threshold of the detector.  Useful for
                            testing the strategy even when the system
                            is currently healthy.

All commands are READ-ONLY — they do NOT run SetEnv, RestartContainer,
or any other side-effect action.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def cmd_probe_gateway_silent():
    from core.recovery.detectors import gateway_silent
    print(f"Running probe: {gateway_silent.DETECTOR_ID}")
    print(f"  ENV_PATH = {gateway_silent.ENV_PATH}")
    print(f"  BROKEN_CONFIRMATIONS = {gateway_silent.BROKEN_CONFIRMATIONS}")
    print("")
    event = gateway_silent.probe()
    print("=== HealthEvent ===")
    print(json.dumps(event.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_dry_run_classA(force_broken: bool = False):
    from core.recovery.detectors import gateway_silent
    from core.recovery.events import HealthEvent, HealthState
    from core.recovery.executor import RecoveryExecutor
    from core.recovery.strategies import classA_revert_webhook

    # Build the strategy.  Use a throwaway env_path so a real-run would
    # not alter anything important, though dry_run means nothing runs.
    strategy = classA_revert_webhook.build(
        env_path="/tmp/recovery_simulate.env",
        compose_dir="/tmp",
    )
    executor = RecoveryExecutor(
        strategies=[strategy],
        dry_run_global=True,  # GLOBAL dry-run: NO side effects
    )

    if force_broken:
        print("[simulate] Building synthetic BROKEN event matching Class A")
        event = HealthEvent(
            detector_id="gateway_silent",
            state=HealthState.BROKEN,
            evidence={
                "mode": "webhook",
                "webhook_url_present": False,
                "reason": "USE_WEBHOOK=1 but Telegram reports no webhook URL registered",
                "consecutive_failures": 3,
                "threshold": 3,
            },
        )
    else:
        print("[simulate] Running real probe and using its event (may be OK)")
        event = gateway_silent.probe()

    print(f"\n[simulate] Event: state={event.state.value}, detector={event.detector_id}")
    if event.evidence:
        print(f"[simulate] Evidence:")
        for k, v in event.evidence.items():
            print(f"    {k} = {v}")

    if not event.is_actionable():
        print("\n[simulate] Event is not BROKEN; executor will NOT act.")
        print("[simulate] Use --force-broken to test the strategy anyway.")
        return 0

    print("\n[simulate] Dispatching to executor in DRY-RUN mode...")
    print("[simulate] (double-probe gate requires 2 consecutive BROKENs to trigger)")
    print("=" * 60)
    # 1st BROKEN — arms the gate but doesn't trigger
    executor.on_event(event)
    # 2nd BROKEN — satisfies gate, runs strategy in dry-run
    executor.on_event(event)
    print("=" * 60)
    print("[simulate] Done. See logs above for the action plan.")
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    force_broken = "--force-broken" in argv[1:]

    if cmd == "probe-gateway-silent":
        return cmd_probe_gateway_silent()
    if cmd == "dry-run-classA":
        return cmd_dry_run_classA(force_broken=force_broken)

    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
