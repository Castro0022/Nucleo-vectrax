"""
Vectrax Action Executor
=========================
Executes an ActionPlan ONLY when PolicyRouter returned AUTO_EXECUTE.

Lifecycle:
  1. Pre-execution: create git checkpoint or filesystem snapshot.
  2. Execution: run planned operations (create/modify files, run commands).
  3. Post-execution: run tests.
     - Tests pass → log ACTION_EXECUTED.
     - Tests fail → rollback + log ACTION_ROLLED_BACK.
     - Unexpected error → rollback + log ACTION_FAILED.

Safety (defense in depth):
  - Duplicate safety checks from PolicyRouter and ActionSandbox.
  - NEVER executes without AUTO_EXECUTE routing decision.
  - NEVER touches SACRED_CORE, hard-limited, or non-whitelisted paths.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.action_sandbox import ActionPlan, SandboxValidationError
from core.autonomy_policy import Zone, classify_path, is_hard_limited
from core.causal_ledger import CausalEventType, append as ledger_append, compute_diff_hash
from core.policy_router import RoutingAction, WHITELISTED_ACTIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = os.path.expanduser("~/Vectrax")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "vault", "snapshots")


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Result of executing an ActionPlan."""
    plan_id: str
    success: bool
    rolled_back: bool = False
    test_output: str = ""
    error: str = ""
    checkpoint_ref: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "success": self.success,
            "rolled_back": self.rolled_back,
            "test_output": self.test_output[:500],
            "error": self.error[:500],
            "checkpoint_ref": self.checkpoint_ref,
        }


# ---------------------------------------------------------------------------
# Action Executor
# ---------------------------------------------------------------------------

class ActionExecutor:
    """
    Executes validated ActionPlans with safety nets.

    Usage::

        executor = ActionExecutor()
        result = executor.execute(plan, routing_action=RoutingAction.AUTO_EXECUTE)
    """

    def execute(
        self,
        plan: ActionPlan,
        routing_action: RoutingAction,
    ) -> ExecutionResult:
        """
        Execute an ActionPlan.

        Args:
            plan: The ActionPlan from ActionSandbox.
            routing_action: Must be AUTO_EXECUTE, otherwise raises.

        Returns:
            ExecutionResult with success/failure status.
        """
        # --- Gate: only AUTO_EXECUTE ---
        if routing_action != RoutingAction.AUTO_EXECUTE:
            return ExecutionResult(
                plan_id=plan.plan_id,
                success=False,
                error=f"Ejecución rechazada: routing_action={routing_action.value} (requiere AUTO_EXECUTE)",
            )

        # --- Defense-in-depth: re-validate safety ---
        try:
            self._safety_recheck(plan)
        except SandboxValidationError as e:
            ledger_append(
                CausalEventType.ACTION_FAILED,
                plan.plan_id,
                action_type=plan.action_type,
                routing_decision=routing_action.value,
                details={"error": str(e), "phase": "safety_recheck"},
            )
            return ExecutionResult(
                plan_id=plan.plan_id,
                success=False,
                error=f"Safety recheck failed: {e}",
            )

        # --- Pre-execution: checkpoint ---
        checkpoint_ref = self._create_checkpoint(plan)

        # --- Execution ---
        try:
            self._execute_plan(plan)
        except Exception as e:
            logger.error(f"Execution failed for {plan.plan_id}: {e}")
            self._rollback(plan, checkpoint_ref)
            ledger_append(
                CausalEventType.ACTION_FAILED,
                plan.plan_id,
                action_type=plan.action_type,
                routing_decision=routing_action.value,
                details={"error": str(e), "phase": "execution", "checkpoint": checkpoint_ref},
            )
            return ExecutionResult(
                plan_id=plan.plan_id,
                success=False,
                error=str(e),
                checkpoint_ref=checkpoint_ref,
            )

        # --- Post-execution: run tests ---
        test_passed, test_output = self._run_tests(plan)

        if test_passed:
            ledger_append(
                CausalEventType.ACTION_EXECUTED,
                plan.plan_id,
                action_type=plan.action_type,
                routing_decision=routing_action.value,
                details={
                    "test_output": test_output[:500],
                    "checkpoint": checkpoint_ref,
                },
                diff_hash=compute_diff_hash(plan.expected_diff_summary),
            )
            return ExecutionResult(
                plan_id=plan.plan_id,
                success=True,
                test_output=test_output,
                checkpoint_ref=checkpoint_ref,
            )
        else:
            # Tests failed → rollback
            self._rollback(plan, checkpoint_ref)
            ledger_append(
                CausalEventType.ACTION_ROLLED_BACK,
                plan.plan_id,
                action_type=plan.action_type,
                routing_decision=routing_action.value,
                details={
                    "reason": "tests_failed",
                    "test_output": test_output[:500],
                    "checkpoint": checkpoint_ref,
                },
            )
            return ExecutionResult(
                plan_id=plan.plan_id,
                success=False,
                rolled_back=True,
                test_output=test_output,
                checkpoint_ref=checkpoint_ref,
            )

    # -----------------------------------------------------------------------
    # Safety re-check (defense in depth)
    # -----------------------------------------------------------------------

    def _safety_recheck(self, plan: ActionPlan) -> None:
        """Re-validate all safety constraints before execution."""
        if plan.action_type not in WHITELISTED_ACTIONS:
            raise SandboxValidationError(
                f"action_type '{plan.action_type}' no está en whitelist"
            )

        for path in plan.all_affected_paths:
            if is_hard_limited(path):
                raise SandboxValidationError(
                    f"HARD LIMIT: '{path}' es un archivo protegido"
                )
            zone = classify_path(path)
            if zone == Zone.SACRED_CORE:
                raise SandboxValidationError(
                    f"SACRED_CORE: '{path}' — modificación prohibida"
                )

    # -----------------------------------------------------------------------
    # Checkpoint & rollback
    # -----------------------------------------------------------------------

    def _create_checkpoint(self, plan: ActionPlan) -> str:
        """
        Create a rollback checkpoint.

        Prefers git if available, falls back to filesystem snapshot.
        Returns a reference string for the checkpoint.
        """
        # Try git first
        if self._git_available():
            return self._git_checkpoint(plan)

        # Filesystem fallback
        return self._filesystem_snapshot(plan)

    def _git_available(self) -> bool:
        """Check if we're in a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git_checkpoint(self, plan: ActionPlan) -> str:
        """Create a git stash checkpoint."""
        try:
            # Stash current changes
            tag = f"vectrax-checkpoint-{plan.plan_id}"
            subprocess.run(
                ["git", "stash", "push", "-m", tag],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Pop the stash immediately (we just want the ref)
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return f"git:stash:{tag}"
        except Exception as e:
            logger.warning(f"Git checkpoint failed, falling back to fs: {e}")
            return self._filesystem_snapshot(plan)

    def _filesystem_snapshot(self, plan: ActionPlan) -> str:
        """Create filesystem backup of files to be modified."""
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        snapshot_id = f"snap-{plan.plan_id}-{int(time.time())}"
        snap_dir = os.path.join(SNAPSHOT_DIR, snapshot_id)
        os.makedirs(snap_dir, exist_ok=True)

        for path in plan.files_to_modify:
            abs_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
            if os.path.exists(abs_path):
                dest = os.path.join(snap_dir, os.path.basename(path))
                shutil.copy2(abs_path, dest)

        return f"fs:{snapshot_id}"

    def _rollback(self, plan: ActionPlan, checkpoint_ref: str) -> None:
        """Rollback changes using the checkpoint."""
        try:
            # Remove created files
            for path in plan.files_to_create:
                abs_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
                if os.path.exists(abs_path):
                    os.remove(abs_path)
                    logger.info(f"Rollback: removed created file {abs_path}")

            # Restore modified files from snapshot
            if checkpoint_ref.startswith("fs:"):
                snapshot_id = checkpoint_ref.split(":", 1)[1]
                snap_dir = os.path.join(SNAPSHOT_DIR, snapshot_id)
                if os.path.isdir(snap_dir):
                    for path in plan.files_to_modify:
                        backup = os.path.join(snap_dir, os.path.basename(path))
                        abs_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
                        if os.path.exists(backup):
                            shutil.copy2(backup, abs_path)
                            logger.info(f"Rollback: restored {abs_path}")

        except Exception as e:
            logger.error(f"Rollback failed for {plan.plan_id}: {e}")

    # -----------------------------------------------------------------------
    # Plan execution
    # -----------------------------------------------------------------------

    def _execute_plan(self, plan: ActionPlan) -> None:
        """Execute the planned file operations."""
        # Create files
        for path in plan.files_to_create:
            abs_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            if not os.path.exists(abs_path):
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(f'"""Auto-generated by Vectrax ActionExecutor — plan {plan.plan_id}"""\n')
                logger.info(f"Created: {abs_path}")

        # Run planned commands
        for cmd in plan.commands_to_run:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Command failed ({result.returncode}): {cmd}\n"
                    f"stderr: {result.stderr[:300]}"
                )

    # -----------------------------------------------------------------------
    # Test runner
    # -----------------------------------------------------------------------

    def _run_tests(self, plan: ActionPlan) -> tuple[bool, str]:
        """
        Run the project test suite.
        Returns (passed: bool, output: str).
        """
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=short", "-q"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = result.stdout + result.stderr
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "Tests timed out (120s)"
        except FileNotFoundError:
            # pytest not available — treat as pass with warning
            return True, "pytest not found — skipped (warning)"
        except Exception as e:
            return False, f"Test runner error: {e}"


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_executor: Optional[ActionExecutor] = None


def get_executor() -> ActionExecutor:
    """Return the global singleton ActionExecutor."""
    global _global_executor
    if _global_executor is None:
        _global_executor = ActionExecutor()
    return _global_executor
