# Vectrax Intelligence Router (ModelRouter)

## Overview

The Intelligence Router automatically selects the best provider/model for each task.
It operates silently — the user only sees the final answer. Routing decisions are
logged to the audit ledger but hidden from output unless `VX_DEBUG=1` is set.

## Architecture

```
Prompt → TaskClassifier → CapabilityRegistry → ScoringEngine → RiskCheck → Decision
                                                                    ↓
                                                              AuditLedger
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| Capability Registry | `core/routing/capabilities.py` | Maps models to capabilities, cost, latency, context limits |
| Task Classifier | `core/routing/task_classifier.py` | Infers task type from prompt using rules-first approach |
| Model Router | `core/routing/model_router.py` | Scores candidates and selects primary + fallback |
| Circuit Breaker | `core/routing/circuit_breaker.py` | Tracks provider failures, skips unhealthy providers |

## Capabilities

The registry tracks 8 capabilities per model:

- **reasoning** — logical analysis, math, problem-solving
- **coding** — code generation, debugging, refactoring
- **summarization** — condensing text, key points extraction
- **translation** — language translation
- **vision** — image understanding (requires `image` modality)
- **structured_extraction** — JSON/CSV parsing, schema extraction
- **long_context** — handling prompts > 32K tokens
- **tool_use** — function calling, tool integration

## Registered Models

### Local (Ollama) — always available
- `ollama:llama3.2:3b` — reasoning, coding, summarization, translation (8K ctx)
- `ollama:qwen2.5-coder:7b` — coding, reasoning, structured extraction, tool use (32K ctx)
- `ollama:llama3.1:8b` — reasoning, coding, summarization, translation, long context (128K ctx)
- `ollama:llava:7b` — vision, reasoning, summarization (4K ctx)

### Cloud (optional, env-var activated)
- `openai:gpt-4o` — all capabilities (128K ctx) — requires `OPENAI_API_KEY`
- `openai:gpt-4o-mini` — most capabilities (128K ctx) — requires `OPENAI_API_KEY`
- `gemini:gemini-2.0-flash` — most capabilities (1M ctx) — requires `GEMINI_API_KEY`
- `anthropic:claude-opus-4-7` — most capabilities (1M ctx) — requires `ANTHROPIC_API_KEY`

## Task Classification

The classifier uses a rules-first approach (no external calls):

1. **Regex patterns** — keyword matching for each task type (highest confidence)
2. **Length heuristic** — prompt > 6000 chars → `LONG_CONTEXT`
3. **Structural heuristic** — JSON/bracket patterns → `STRUCTURED_EXTRACTION`
4. **LLM fallback** — only when `VX_LLM_CLASSIFIER=1` (feature flag)
5. **Default** — falls back to `CHAT`

Supports both English and Spanish keywords.

## Routing Policy

### Scoring Formula

Each candidate model is scored:

```
score = capability_match × 0.4 + cost_score × 0.2 + latency_score × 0.2 + priority_score × 0.2
```

- **capability_match**: 1.0 if model has required capability, 0.3 otherwise
- **cost_score**: `1.0 - (cost / 0.01)` — local models get 1.0 (free)
- **latency_score**: `1.0 - (latency / 5000ms)` — faster is better
- **priority_score**: `1.0 - (priority / 100)` — lower priority number is better

### Candidate Selection

1. Filter registry by required capability
2. Exclude providers with open circuit breakers
3. In `recover` governor mode → local-only
4. Score all candidates → select top as primary, runner-up as fallback

### Risk Integration

Before returning, the router consults the Risk Engine:
- Creates an `OperationContext` with `op_type="llm_generate"` and the detected task type
- If risk is **CRITICAL** and governor mode ≠ `act` → routing is **blocked**
- Otherwise, routing proceeds normally
- Sacred core protections via `autonomy_policy.py` remain unchanged

## Audit Logging

Every routing decision is recorded in the audit ledger:

```python
audit_ledger.record(
    action="model_route",
    actor="model_router",
    decision="approved",  # or "blocked"
    reason="task=code | cap=coding | score=0.892",
    metadata={
        "primary_provider": "ollama",
        "primary_model": "qwen2.5-coder:7b",
        "task_type": "code",
        "task_confidence": 0.8,
        "score": 0.892,
        ...
    }
)
```

Routing details are **not shown to the user** unless `VX_DEBUG=1`.

## CLI Commands

### `vx route status`

Shows router health, model registry, circuit breaker states, and recent decisions.

### `vx route test`

Runs the routing test suite (`tests/test_model_router.py`).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (none) | Enables OpenAI cloud models |
| `GEMINI_API_KEY` | (none) | Enables Gemini cloud models |
| `ANTHROPIC_API_KEY` | (none) | Enables Anthropic cloud models |
| `VX_DEBUG` | (none) | Set to `1` to print routing decisions |
| `VX_LLM_CLASSIFIER` | (none) | Set to `1` to enable LLM-based task classification |
| `VX_DAILY_BUDGET_CAP` | 0 | Daily token budget cap (0 = unlimited) |

## Adding New Models

Add a `ModelProfile` to the `_build_default_registry()` function in
`core/routing/capabilities.py`:

```python
ModelProfile(
    provider="ollama",
    model="my-new-model:latest",
    capabilities={Capability.CODING, Capability.REASONING},
    cost_per_1k_tokens=0.0,
    avg_latency_ms=1000,
    max_context_tokens=16384,
    priority=10,
    is_local=True,
)
```
