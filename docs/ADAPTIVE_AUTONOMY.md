# Adaptive Autonomy Mode

Vectrax automatically selects the safest operating mode for each operation based on real-time context and risk signals. No manual mode flag is required — the system derives the mode from the operation's risk profile.

## Modes

| Mode | Behaviour |
|------|-----------|
| **AUTO** | Default. Permits provider fallback, LLM classification, and cloud routing. Suitable for low-risk, high-confidence tasks. |
| **MISSION_STRICT** | Fail-closed. Disables non-deterministic fallback, requires confirmation for irreversible actions, uses only verified local providers. Triggered automatically when risk signals exceed safe thresholds. |

## Architecture

```
Prompt ──▶ TaskClassifier ──▶ OperationEnvelope ──▶ EscalationDetector
                                                        │
                                               EscalationResult
                                                        │
                                               AdaptiveAutonomyPolicy
                                                        │
                                               PolicyDecision (mode + constraints)
                                                        │
                                               ModelRouter applies constraints
```

### Components

- **`core/escalation_detector.py`** — `OperationEnvelope` (input), `EscalationDetector` (6 trigger checks), `EscalationResult` (output with triggers + severity).
- **`core/adaptive_autonomy.py`** — `AutonomyMode` enum, `PolicyDecision` dataclass, `AdaptiveAutonomyPolicy` (consumes `EscalationResult`, returns mode + constraints, writes audit).
- **`core/routing/model_router.py`** — `_evaluate_autonomy()` hook builds envelope from classification and governor mode, applies constraints to routing.

## Escalation Triggers

The `EscalationDetector` fires on **any** of these conditions:

1. **`critical_risk`** — Risk level is CRITICAL (severity 1.0).
2. **`high_risk_non_act`** — Risk level is HIGH and governor mode is not `act` (severity 0.8).
3. **`sacred_core`** — Operation touches a sacred-core path (e.g. `core/governor.py`, `core/risk_engine.py`) (severity 0.9).
4. **`privileged_op`** — Operation type is privileged: `autopatch`, `role_change`, `config_override` (severity 0.85).
5. **`irreversible`** — Irreversibility flags are present (e.g. `writes_files`, `modifies_state`) (severity 0.8).
6. **`sensitive_exposure`** — Exposure flags indicate external sensitive domains (e.g. `cloud_provider`, `api_key`) (severity 0.75).
7. **`low_confidence`** — Classification confidence falls below the configurable minimum (severity 0.6).

If **any** trigger fires → mode escalates to `MISSION_STRICT`.

## Constraints by Mode

### AUTO
- `fallback_allowed`: true
- `confirmation_required`: false
- `local_only`: false
- `verified_providers_only`: false
- `llm_classifier`: true
- `provider_switching`: true

### MISSION_STRICT
- `fallback_allowed`: false
- `confirmation_required`: true
- `local_only`: true
- `verified_providers_only`: true
- `llm_classifier`: false
- `provider_switching`: false

In `MISSION_STRICT`, the ModelRouter:
- Sets `fallback` to `None`
- Forces `local_only=True` for candidate selection
- Disables LLM classifier fallback
- Appends mode info to the routing reason string

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VX_MIN_CONFIDENCE` | `0.6` | Minimum classification confidence. Below this → `low_confidence` trigger fires. |

No other manual configuration is needed. Mode is derived automatically from context.

## Audit Trail

Every escalation to `MISSION_STRICT` writes an audit entry:

- **action**: `autonomy_escalation`
- **actor**: `autonomy_policy`
- **decision**: `escalated`
- **metadata**: `{ mode, triggers, severity, task_type, governor_mode }`

AUTO mode operations do **not** generate escalation audit entries.

Entries are stored in the append-only audit ledger at `~/Vectrax/vault/audit_ledger.db`.

## Backward Compatibility

- All existing `RoutingDecision` fields remain unchanged.
- Two new optional fields added: `autonomy_mode` (str) and `autonomy_constraints` (dict).
- Existing CLI commands (`vx route status`, `vx route test`, etc.) work without changes.
- If the autonomy evaluation fails for any reason, the router falls back to its previous behaviour (fail-open for autonomy, not for risk).

## Tests

```
pytest tests/test_adaptive_autonomy.py -v
```

36 tests covering:
- Each escalation trigger individually
- Combined triggers
- Policy mode derivation (AUTO / MISSION_STRICT)
- Constraint verification for both modes
- ModelRouter integration (autonomy fields, fallback behaviour)
- Audit logging on escalation
- Singletons and convenience functions
- Edge cases (empty envelope, unknown risk level)
