"""
Vectrax Cognition Governor
===========================
Policy engine that evaluates cognitive state each cycle and sets an
operational mode that controls what actions the daemon may take.

Modes:
  observe  — first cycles after boot; system stabilizing, no autopatch.
  act      — healthy; all actions (including autopatch) allowed.
  cautious — minor issues detected; actions allowed but with warnings.
  recover  — errors >= 2 in last 10 cycles; autopatch disabled,
             only smoke + log. Auto-returns to cautious after 3
             consecutive clean cycles.

Policy is persisted in cognition_state.json so `vx status` can read it.
"""

from datetime import datetime

from core import state_manager
from core.risk_engine import (
    OperationContext,
    RiskEngine,
    RiskLevel,
    get_risk_engine,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OBSERVE_CYCLES = 3        # stay in observe for first N cycles after boot
ERROR_WINDOW = 10         # rolling window size for error history
RECOVER_THRESHOLD = 2     # errors in window to trigger recover
CLEAN_STREAK_EXIT = 3     # consecutive clean cycles to leave recover

MODES = ("observe", "cautious", "act", "recover")


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Error history helpers
# ---------------------------------------------------------------------------

def push_cycle_result(state, had_error):
    """Append this cycle's error flag to the rolling window."""
    history = state.get("error_history", [])
    history.append(had_error)
    # Keep only last ERROR_WINDOW entries
    if len(history) > ERROR_WINDOW:
        history = history[-ERROR_WINDOW:]
    state["error_history"] = history
    return history


def _errors_in_window(history):
    """Count True values (error cycles) in the window."""
    return sum(1 for x in history if x)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate(had_error=False):
    """
    Evaluate policy for this cycle. Must be called BEFORE actions.

    Args:
        had_error: whether the previous cycle produced an error.

    Returns:
        dict with policy decision:
          mode             — one of MODES
          autopatch_allowed — bool
          reason           — human-readable explanation
    """
    state = state_manager.load()
    cycles = state.get("cycles", 0)
    prev_mode = state.get("governor_mode", "observe")
    clean_streak = state.get("clean_streak", 0)

    # Update error history
    history = push_cycle_result(state, had_error)
    errors_in_win = _errors_in_window(history)

    # Update clean streak
    if had_error:
        clean_streak = 0
    else:
        clean_streak += 1
    state["clean_streak"] = clean_streak

    # --- Decision logic ---

    mode = prev_mode
    reason = ""

    # Rule 1: First N cycles → observe
    if cycles < OBSERVE_CYCLES:
        mode = "observe"
        reason = f"Stabilizing (cycle {cycles}/{OBSERVE_CYCLES})"

    # Rule 2: Recover trigger — errors >= threshold in window
    elif errors_in_win >= RECOVER_THRESHOLD:
        mode = "recover"
        reason = f"{errors_in_win} errors in last {len(history)} cycles (threshold={RECOVER_THRESHOLD})"

    # Rule 3: Exit recover — need CLEAN_STREAK_EXIT consecutive clean cycles
    elif prev_mode == "recover":
        if clean_streak >= CLEAN_STREAK_EXIT:
            mode = "cautious"
            reason = f"Exiting recover after {clean_streak} consecutive clean cycles"
        else:
            mode = "recover"
            reason = f"Still recovering — {clean_streak}/{CLEAN_STREAK_EXIT} clean cycles"

    # Rule 4: Cautious → act after sustained health
    elif prev_mode == "cautious" and errors_in_win == 0 and clean_streak >= CLEAN_STREAK_EXIT:
        mode = "act"
        reason = "Sustained health — full autonomy"

    # Rule 5: Healthy with no recent errors → act
    elif errors_in_win == 0 and cycles >= OBSERVE_CYCLES:
        mode = "act"
        reason = "Nominal — all systems healthy"

    # Rule 6: Some errors but below threshold → cautious
    elif errors_in_win > 0:
        mode = "cautious"
        reason = f"{errors_in_win} error(s) in window — proceeding with caution"

    # Fallback
    else:
        mode = "act"
        reason = "Default — healthy"

    # Derive permissions from mode
    autopatch_allowed = mode in ("act", "cautious")

    # --- Risk engine assessment (best-effort) ---
    risk_score = None
    risk_level = None
    try:
        engine = get_risk_engine()
        ctx = OperationContext(
            governor_mode=mode,
            error_history=history,
        )
        assessment = engine.assess(ctx)
        risk_score = assessment.risk_score
        risk_level = assessment.risk_level.value

        # Block CRITICAL-risk operations outside of 'act' mode
        if assessment.risk_level == RiskLevel.CRITICAL and mode != "act":
            autopatch_allowed = False
            reason += f" | CRITICAL risk ({risk_score:.2f}) — autopatch blocked"
    except Exception:
        pass  # risk engine failure must not break governance

    policy = {
        "mode": mode,
        "autopatch_allowed": autopatch_allowed,
        "reason": reason,
        "risk_score": risk_score,
        "risk_level": risk_level,
    }

    # Persist into state
    state["governor_mode"] = mode
    state["governor_reason"] = reason
    state["governor_decided_at"] = _now_iso()
    state["governor_autopatch_allowed"] = autopatch_allowed
    state["governor_risk_score"] = risk_score
    state["governor_risk_level"] = risk_level
    state_manager.save(state)

    return policy


def get_current_policy():
    """Read persisted policy from state (for vx status, etc.)."""
    state = state_manager.load()
    return {
        "mode": state.get("governor_mode", "unknown"),
        "autopatch_allowed": state.get("governor_autopatch_allowed", False),
        "reason": state.get("governor_reason", ""),
        "decided_at": state.get("governor_decided_at"),
        "clean_streak": state.get("clean_streak", 0),
        "error_history": state.get("error_history", []),
    }
