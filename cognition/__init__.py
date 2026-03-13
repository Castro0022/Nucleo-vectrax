"""
Vectrax Cognitive Layer
========================
Four integrated motors on top of the Universal Connection Engine:

  1. Perception  — world observation, signal intake, context building
  2. Reasoning   — strategic analysis, risk, impact, scenarios
  3. Memory      — deep structural memory, knowledge graph
  4. Orchestrator — cognitive loop, proposal generation, traceability

Nothing in this package modifies the existing core, connectors, or
pipeline subsystems.  All imports are unidirectional:
cognition → core/connectors/pipeline (never the reverse).
"""

from __future__ import annotations

__version__ = "0.1.0"
