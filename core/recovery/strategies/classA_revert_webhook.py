"""
Clase A strategy — auto-revert USE_WEBHOOK when the gateway is silent.

When does this fire?
  detector "gateway_silent" emits BROKEN with evidence including:
    - mode: "webhook"
    - webhook_url_present: False (OR explicit reason containing
      "USE_WEBHOOK=1 but Telegram reports no webhook URL")

Precondition guard:
  We ONLY match if BOTH conditions hold:
    (1) mode == "webhook" in the evidence
    (2) evidence indicates no webhook is actually registered
  Without BOTH, we are NOT looking at the 10h bug; some other strategy
  (future Class B) or human escalation should handle the case.

Action plan:
  1. SetEnv USE_WEBHOOK=0 in /app/.env    → flip back to long-poll.
  2. RestartContainer vectrax (mode=up)  → recreate with new .env.
  3. NotifyHuman with context            → Mario needs to know a
     recovery happened AND that the webhook migration is pending.

Rollback:
  If action 1 succeeds but action 2 fails, we need to put USE_WEBHOOK
  back where it was.  (Rare: SetEnv rarely fails.) Conservative rollback
  sets USE_WEBHOOK=1 again to reflect pre-action state.

Throttle:
  Default: max 3 attempts per 30 min window.  If the bot ALSO has the
  USE_WEBHOOK=1 bug AND the rollback doesn't fix it (e.g., container
  won't start), escalate after 3 tries to avoid reboot loop.

Human approval:
  This strategy modifies persistent .env — a high-impact change.
  In v1 we allow it automatic because:
    - the rollback is well-defined and safe
    - failing to recover costs more (10h of silence) than a false trigger
  If you change your mind, flip require_human_approval=True.
"""

from __future__ import annotations

from typing import Any, Dict

from core.recovery.actions import (
    NotifyHuman,
    RestartContainer,
    SetEnv,
)
from core.recovery.strategies import RecoveryStrategy

STRATEGY_ID = "classA_revert_webhook"


def _precondition(evidence: Dict[str, Any]) -> bool:
    """True only when ALL bug-signature indicators match (AND, not OR).

    Three hard conditions — every one must hold — to avoid false positives:
      1. evidence["mode"] == "webhook"                   (USE_WEBHOOK=1 in env)
      2. evidence["webhook_url_present"] == False         (Telegram reports no URL)
      3. evidence["reason"] contains the bug-specific phrase
         "USE_WEBHOOK=1 but Telegram" (case-insensitive)

    Extra safety (already in detector): BROKEN_CONFIRMATIONS=3 consecutive
    failing probes before even emitting BROKEN.  Combined with the
    executor-level 2-consecutive-BROKEN gate, the strategy needs 5 probes
    of sustained failure (~5 min) before it ever runs.
    """
    if evidence.get("mode") != "webhook":
        return False
    if evidence.get("webhook_url_present") is not False:  # must be explicitly False
        return False
    reason = str(evidence.get("reason", "")).lower()
    if "use_webhook=1 but telegram" not in reason:
        return False
    return True


def build(env_path: str = "/app/.env", compose_dir: str = "/opt/vectrax") -> RecoveryStrategy:
    """Factory — constructs the strategy with paths injectable for tests."""
    # Post-action NotifyHuman is MANDATORY and carries an explicit diff
    # so the operator can audit what changed without reading logs.
    audit_message = (
        "[classA] AUTO-RECOVERY EXECUTED\n"
        f"  detector:     gateway_silent (BROKEN after >=3 consecutive probes)\n"
        f"  strategy:     classA_revert_webhook\n"
        f"  diff:         USE_WEBHOOK: 1 -> 0 (in {env_path})\n"
        f"  action:       docker compose up -d vectrax (cwd={compose_dir})\n"
        f"  root cause:   USE_WEBHOOK=1 but Telegram reports no webhook URL registered.\n"
        f"  next step:    either finish DNS + setWebhook migration,\n"
        f"                 or leave USE_WEBHOOK=0 until the migration is ready.\n"
        f"  rollback:     to restore USE_WEBHOOK=1, edit {env_path} and restart container."
    )
    action_plan = [
        SetEnv(env_file=env_path, key="USE_WEBHOOK", value="0"),
        RestartContainer(
            container_name="vectrax",
            compose_dir=compose_dir,
            mode="up",  # recreate to pick up .env changes
        ),
        NotifyHuman(
            message=audit_message,
            channel="file",
        ),
    ]
    rollback_plan = [
        # If RestartContainer failed, at least revert the flag so the
        # env matches the old (pre-strategy) state.
        SetEnv(env_file=env_path, key="USE_WEBHOOK", value="1"),
        NotifyHuman(
            message=(
                f"[classA] Rollback: RestartContainer failed after flipping "
                f"USE_WEBHOOK=0. Reverted to USE_WEBHOOK=1. Manual intervention "
                f"required — container may be unable to start."
            ),
            channel="file",
        ),
    ]
    return RecoveryStrategy(
        id=STRATEGY_ID,
        trigger_events=["gateway_silent"],
        precondition=_precondition,
        action_plan=action_plan,
        rollback_plan=rollback_plan,
        # Throttle 2/60 (was 3/30): a second attempt confirms the revert
        # didn't stick (likely something external is pushing USE_WEBHOOK=1);
        # third attempt = flapping territory → escalate instead.
        max_attempts_per_window=2,
        cooldown_s=3600,  # 60 min
        escalation_target="file",
        require_human_approval=False,
        description=(
            "Reverts USE_WEBHOOK=1 to 0 and restarts container when "
            "Telegram reports no webhook is registered. Recovers from "
            "the Apr-22 10h silence bug. AND-precondition + 2-consecutive-BROKEN "
            "gate + 2/60 throttle prevent flapping."
        ),
    )
