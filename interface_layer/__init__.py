"""
Vectrax Interface Layer
========================
External subsystem that mediates human interaction with the Vectrax core
without modifying immutable core components.

All requests flow through: IntentGate → SimulationSandbox → RiskEvaluator → HumanConfirmation.
"""

from interface_layer.intent_gate import IntentGate
from interface_layer.simulation_sandbox import SimulationSandbox
from interface_layer.risk_evaluator import RiskEvaluator
from interface_layer.human_confirmation import HumanConfirmation

__all__ = [
    "IntentGate",
    "SimulationSandbox",
    "RiskEvaluator",
    "HumanConfirmation",
]
