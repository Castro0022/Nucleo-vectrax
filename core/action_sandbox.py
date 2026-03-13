"""
Vectrax Action Sandbox
========================
Dry-run planning engine that produces an ActionPlan WITHOUT executing anything.

Responsibilities:
  1. Validate action_type is whitelisted.
  2. Validate all affected paths against autonomy_policy zones.
  3. Build a plan describing files to create/modify, commands to run.
  4. Log ACTION_PLANNED to CausalLedger.
  5. Return the plan for review or execution.

The sandbox NEVER executes — it only describes what WOULD happen.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.autonomy_policy import Zone, classify_path, is_hard_limited
from core.causal_ledger import CausalEventType, append as ledger_append, compute_diff_hash
from core.policy_router import WHITELISTED_ACTIONS


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ActionPlan:
    """Describes a planned action without executing it."""
    plan_id: str
    action_type: str
    description: str
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    commands_to_run: List[str] = field(default_factory=list)
    expected_diff_summary: str = ""
    rollback_strategy: str = ""
    timestamp: float = field(default_factory=time.time)
    routing_context: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_affected_paths(self) -> List[str]:
        return self.files_to_create + self.files_to_modify

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "action_type": self.action_type,
            "description": self.description,
            "files_to_create": self.files_to_create,
            "files_to_modify": self.files_to_modify,
            "commands_to_run": self.commands_to_run,
            "expected_diff_summary": self.expected_diff_summary,
            "rollback_strategy": self.rollback_strategy,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class SandboxValidationError(Exception):
    """Raised when sandbox validation fails."""
    pass


# ---------------------------------------------------------------------------
# Action Sandbox
# ---------------------------------------------------------------------------

class ActionSandbox:
    """
    Produces ActionPlans after validating safety constraints.

    Usage::

        sandbox = ActionSandbox()
        plan = sandbox.plan(
            action_type="create_module",
            description="Create observability dashboard module",
            files_to_create=["core/dashboard.py"],
            files_to_modify=["core/__init__.py"],
            commands_to_run=["python -m pytest tests/"],
        )
    """

    # Commands allowed in plans (prefix-match)
    SAFE_COMMAND_PREFIXES = (
        "python -m pytest",
        "pytest",
        "python -c",
        "ruff ",
        "mypy ",
        "flake8 ",
        "black --check",
    )

    def plan(
        self,
        action_type: str,
        description: str,
        files_to_create: Optional[List[str]] = None,
        files_to_modify: Optional[List[str]] = None,
        commands_to_run: Optional[List[str]] = None,
        routing_context: Optional[Dict[str, Any]] = None,
    ) -> ActionPlan:
        """
        Create a dry-run ActionPlan.

        Raises SandboxValidationError on any safety violation.
        Logs ACTION_PLANNED to CausalLedger on success.
        """
        files_to_create = files_to_create or []
        files_to_modify = files_to_modify or []
        commands_to_run = commands_to_run or []
        routing_context = routing_context or {}

        # --- 1. Validate action type ---
        self._validate_action_type(action_type)

        # --- 2. Validate paths ---
        all_paths = files_to_create + files_to_modify
        self._validate_paths(all_paths)

        # --- 3. Validate commands ---
        self._validate_commands(commands_to_run)

        # --- 4. Build plan ---
        plan_id = f"ap-{uuid.uuid4().hex[:8]}"

        rollback = self._determine_rollback(files_to_create, files_to_modify)
        diff_summary = self._build_diff_summary(
            action_type, description, files_to_create, files_to_modify,
        )

        plan = ActionPlan(
            plan_id=plan_id,
            action_type=action_type,
            description=description,
            files_to_create=files_to_create,
            files_to_modify=files_to_modify,
            commands_to_run=commands_to_run,
            expected_diff_summary=diff_summary,
            rollback_strategy=rollback,
            routing_context=routing_context,
        )

        # --- 5. Log to CausalLedger ---
        ledger_append(
            CausalEventType.ACTION_PLANNED,
            plan_id,
            action_type=action_type,
            routing_decision=routing_context.get("routing_action", ""),
            risk_score=routing_context.get("risk_score"),
            polarity_state=routing_context.get("polarity_state", ""),
            tension=routing_context.get("tension"),
            precedent_confidence=routing_context.get("precedent_confidence"),
            details=plan.to_dict(),
            diff_hash=compute_diff_hash(diff_summary),
        )

        return plan

    # -----------------------------------------------------------------------
    # Validation helpers
    # -----------------------------------------------------------------------

    def _validate_action_type(self, action_type: str) -> None:
        if action_type not in WHITELISTED_ACTIONS:
            raise SandboxValidationError(
                f"action_type '{action_type}' no está en whitelist: "
                f"{sorted(WHITELISTED_ACTIONS)}"
            )

    def _validate_paths(self, paths: List[str]) -> None:
        for path in paths:
            # Hard limits
            if is_hard_limited(path):
                raise SandboxValidationError(
                    f"HARD LIMIT: '{path}' es un archivo protegido "
                    "(secreto/credencial/config crítica)"
                )

            # SACRED_CORE zone
            zone = classify_path(path)
            if zone == Zone.SACRED_CORE:
                raise SandboxValidationError(
                    f"SACRED_CORE: '{path}' pertenece al Core Sagrado — "
                    "modificación automática prohibida"
                )

            # Prevent deletion patterns
            if path.startswith("/") and not path.startswith(
                os.path.expanduser("~/Vectrax")
            ):
                raise SandboxValidationError(
                    f"Ruta fuera del proyecto: '{path}' — solo se permiten "
                    "rutas dentro de ~/Vectrax"
                )

    def _validate_commands(self, commands: List[str]) -> None:
        for cmd in commands:
            cmd_stripped = cmd.strip()
            if not any(cmd_stripped.startswith(p) for p in self.SAFE_COMMAND_PREFIXES):
                raise SandboxValidationError(
                    f"Comando no permitido: '{cmd}' — solo se permiten: "
                    f"{self.SAFE_COMMAND_PREFIXES}"
                )

    # -----------------------------------------------------------------------
    # Plan building helpers
    # -----------------------------------------------------------------------

    def _determine_rollback(
        self,
        files_to_create: List[str],
        files_to_modify: List[str],
    ) -> str:
        """Determine the appropriate rollback strategy."""
        strategies = []

        if files_to_create:
            strategies.append(
                f"Eliminar archivos creados: {', '.join(files_to_create)}"
            )

        if files_to_modify:
            strategies.append(
                "Restaurar archivos modificados desde git checkpoint "
                "o snapshot en vault/snapshots/"
            )

        if not strategies:
            return "Sin rollback necesario (plan vacío)"

        return "; ".join(strategies)

    def _build_diff_summary(
        self,
        action_type: str,
        description: str,
        files_to_create: List[str],
        files_to_modify: List[str],
    ) -> str:
        """Build a human-readable diff summary."""
        lines = [f"[{action_type}] {description}"]

        if files_to_create:
            lines.append(f"  Crear: {len(files_to_create)} archivo(s)")
            for f in files_to_create:
                zone = classify_path(f)
                lines.append(f"    + {f} (zona: {zone.value})")

        if files_to_modify:
            lines.append(f"  Modificar: {len(files_to_modify)} archivo(s)")
            for f in files_to_modify:
                zone = classify_path(f)
                lines.append(f"    ~ {f} (zona: {zone.value})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_global_sandbox: Optional[ActionSandbox] = None


def get_sandbox() -> ActionSandbox:
    """Return the global singleton ActionSandbox."""
    global _global_sandbox
    if _global_sandbox is None:
        _global_sandbox = ActionSandbox()
    return _global_sandbox
