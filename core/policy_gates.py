"""
Vectrax Policy Gates
======================
Named gates that guard specific categories of changes.
Each gate returns (allowed: bool, reason: str).

Gates:
  sacred_core     — NEVER auto-apply, regardless of settings
  docs_tests_logs — low-risk category, default OFF (switchable)
"""

from __future__ import annotations

from typing import Tuple

from core.autonomy_policy import Zone, classify_path, load_autonomy_config


def gate_sacred_core(path: str) -> Tuple[bool, str]:
    """
    Sacred Core gate: changes to core sacred files are NEVER auto-applied.
    This gate cannot be overridden by any switch.
    """
    zone = classify_path(path)
    if zone == Zone.SACRED_CORE:
        return False, f"SACRED_CORE: '{path}' — nunca auto-apply"
    return True, "No es core sagrado"


def gate_docs_tests_logs(path: str) -> Tuple[bool, str]:
    """
    Docs/Tests/Logs gate: low-risk changes.
    Default OFF — can be enabled via config `policy_gates.docs_tests_logs_auto`.
    """
    cfg = load_autonomy_config()
    gates_cfg = cfg.get("policy_gates", {})
    auto_enabled = gates_cfg.get("docs_tests_logs_auto", False)

    zone = classify_path(path)
    if zone == Zone.FLEXIBLE:
        if auto_enabled:
            return True, "FLEXIBLE zone + docs_tests_logs_auto habilitado"
        else:
            return False, "FLEXIBLE zone pero docs_tests_logs_auto desactivado (default OFF)"

    return True, "No aplica gate docs_tests_logs"


def evaluate_gates(path: str) -> Tuple[bool, str]:
    """
    Run all policy gates for a given path.
    Returns (allowed, combined_reason).
    First gate to deny wins.
    """
    gates = [
        ("sacred_core", gate_sacred_core),
        ("docs_tests_logs", gate_docs_tests_logs),
    ]

    for gate_name, gate_fn in gates:
        allowed, reason = gate_fn(path)
        if not allowed:
            return False, f"[{gate_name}] {reason}"

    return True, "All gates passed"
