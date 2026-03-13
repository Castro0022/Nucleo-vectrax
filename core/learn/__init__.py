"""
Vectrax Hybrid Memory & Silent Watchdog
=========================================
Continuous learning subsystem: extracts intent and patterns from the
codebase without storing raw text, secrets, or PII.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VAULT_DIR = os.path.join(PROJECT_ROOT, "vault")
RUNTIME_DIR = os.path.expanduser("~/.vectrax")

EPISODIC_LEDGER_PATH = os.path.join(VAULT_DIR, "episodic_ledger.jsonl")
CANDIDATES_PATH = os.path.join(VAULT_DIR, "candidates.jsonl")
SEMANTIC_INDEX_PATH = os.path.join(VAULT_DIR, "semantic_index.faiss")
SEMANTIC_META_PATH = os.path.join(VAULT_DIR, "semantic_meta.jsonl")
WATCHDOG_PID_PATH = os.path.join(RUNTIME_DIR, "watchdog.pid")

# ---------------------------------------------------------------------------
# Watched directories (relative to PROJECT_ROOT)
# ---------------------------------------------------------------------------

WATCHED_DIRS = ["core", "pipeline", "cli", "models", "services"]

# ---------------------------------------------------------------------------
# Intent taxonomy (compact, <=10)
# ---------------------------------------------------------------------------

INTENTS = [
    "FEATURE_ADD",
    "BUG_FIX",
    "REFACTOR",
    "TEST",
    "CONFIG",
    "DOCS",
    "SECURITY",
    "PERF",
    "INFRA",
    "UNKNOWN",
]

# ---------------------------------------------------------------------------
# Episodic event types
# ---------------------------------------------------------------------------

EVENT_TYPES = [
    "FILE_OBSERVED",
    "STRUCTURE_EXTRACTED",
    "INTENT_INFERRED",
    "PATTERN_CANDIDATE_CREATED",
    "SEMANTIC_INDEX_UPSERTED",
    "ASCENSION_STATE_CHANGED",
    "INGESTION_SUMMARY",
    "CONSTITUTION_ENFORCED",
    "DEAD_END_DETECTED",
    "GRAVITY_RECORDED",
    "GRAVITY_PROMOTED",
    "GRAVITY_DEMOTED",
    "CONSTELLATION_CREATED",
]

# ---------------------------------------------------------------------------
# Ascension candidate states
# ---------------------------------------------------------------------------

CANDIDATE_STATES = ["QUARANTINED", "APPROVED", "BANNED"]

# ---------------------------------------------------------------------------
# Operational modes for intent-driven learning
# ---------------------------------------------------------------------------

LEARN_MODES = ["OBSERVE", "PROPOSE", "PROPOSE_PROCEED", "EXECUTE"]
