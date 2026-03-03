# Architecture C — Single Platform

One codebase, one CLI, three operational modes.

## Overview

Architecture C provides a unified platform for home use and mission-critical operations. The system consists of three layers:

1. **Immutable Core (Constitution)** — Code-enforced invariants that can never be self-modified
2. **Controlled Adaptation** — Versioned, audited policy tuning for operational parameters only
3. **Policy Sandbox** — Propose → simulate → evaluate → promote workflow

## Three Modes

### HOME_AUTO
Default mode. Maximizes usability with minimal prompts.
- Fallback routing allowed
- Provider switching allowed
- LLM classification allowed
- No confirmation required (except invariant-protected operations)
- Audit level: standard

### BUSINESS_GOVERNED
Balanced mode for regulated or business contexts.
- Fallback routing allowed
- Provider switching allowed
- Enhanced audit logging
- Confirmation for sacred-core and irreversible operations

### MISSION_STRICT
Fail-closed mode for critical operations.
- Deterministic routing only (no fallback)
- Verified (local) providers only
- Explicit confirmation required for all operations
- LLM classification disabled
- Audit level: critical

## Auto-Escalation

The `EscalationDetector` derives the effective mode contextually per request:

- **severity == 0** → HOME_AUTO
- **severity < 0.80** → BUSINESS_GOVERNED
- **severity >= 0.80** → MISSION_STRICT

Users can set a *base* mode via `vx mode set`, but the system always escalates upward when risk signals require it. It never escalates downward.

## Hard Invariants (`core/invariants.py`)

Enforced in code, not configurable:

1. **MISSION_STRICT constraints** — `verified_providers_only=true`, `fallback_allowed=false` always enforced
2. **Irreversible risk block** — HIGH/CRITICAL risk never auto-applies irreversible actions
3. **Confidence floor** — Classification confidence ≥ 0.60 enforced
4. **Sacred core protection** — Autopatch cannot modify sacred core paths; any write requires confirmation

## Policy Registry (`core/policy_registry.py`)

SQLite-backed versioned policy storage at `~/Vectrax/vault/policy_registry.db`.

### Tunable Parameters (only these can be auto-tuned)
- `confidence_threshold` (float, floor 0.60)
- `escalation_threshold` (float, 0-1)
- `routing_weights` (dict: capability/cost/latency/priority)
- `fallback_preferences` (list)
- `provider_priority` (dict)

### Policy Lifecycle
```
candidate → (sandbox test) → promote → active
                                         ↓
                                     retired ← rollback
```

Every mutation creates an audit entry. The confidence floor invariant is enforced even in the registry — attempting to set `confidence_threshold < 0.60` auto-corrects to 0.60.

## Policy Sandbox (`core/sandbox_runner.py`)

### Workflow
1. `vx policy propose` — creates candidate
2. `vx sandbox run --policy <id> --ops <N>` — shadow evaluation
3. Review metrics: escalation_rate, stability_score, invariant_violations
4. `vx policy promote <id>` — activate (requires owner role)
5. Auto-rollback if post-promotion monitoring detects degradation

### Metrics
- **escalation_rate** — fraction of ops that triggered any escalation
- **mission_strict_rate** — fraction that hit MISSION_STRICT
- **stability_score** — 1 - variance(severity); higher is better
- **invariant_violations** — must be 0 to promote

### Promotion Rules
- Invariant violations > 0 → blocked
- Stability score < 0.3 → blocked
- Requires `CONFIG_WRITE` permission (owner role)

## CLI Commands

```
vx mode status                          # Show base + effective mode
vx mode set HOME_AUTO                   # Set base mode
vx mode set BUSINESS_GOVERNED
vx mode set MISSION_STRICT

vx policy status                        # Show active policy version
vx policy history                       # Show version history
vx policy propose                       # Create candidate with defaults
vx policy promote <policy_id>           # Promote to active (owner only)
vx policy rollback <policy_id>          # Rollback active policy

vx sandbox run --policy <id> --ops <N>  # Run shadow simulation
```

All existing commands (`vx route status`, `vx propose`, `vx agent`, etc.) continue to work.

## Files

### New
- `core/invariants.py` — Hard invariant checks
- `core/policy_registry.py` — Versioned policy CRUD
- `core/sandbox_runner.py` — Shadow-mode sandbox
- `tests/test_arch_c.py` — 49 tests

### Modified
- `core/adaptive_autonomy.py` — Three modes + registry integration
- `core/escalation_detector.py` — Parameterized thresholds from registry
- `core/routing/model_router.py` — Three-mode handling
- `core/state_manager.py` — `operational_mode` field
- `cli/vx_main.py` — Mode/policy/sandbox CLI commands

## Tests

```
pytest tests/test_arch_c.py -v          # 49 Architecture C tests
pytest tests/ -v                        # Full suite (all tests)
```
