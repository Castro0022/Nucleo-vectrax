# Vectrax

Cognitive memory system that organises experience as a gravitational star graph.

Every event is a **star**. Related stars form **constellations**. Patterns are scored
by **gravity** (repetition × coherence × success rate) and reorganise themselves
toward the core over time. Dense, high-gravity clusters generate **proposals** for
structural evolution — which always require creator approval before execution.

## Install

```bash
cd ~/vectrax
pip install -e .
# Optional FAISS acceleration for large corpora (>1000 stars):
pip install -e ".[faiss]"
```

> First run will download the `all-MiniLM-L6-v2` model (~90 MB) automatically.

## Commands

```
vectrax ingest "<text>" [--success]   Capture an event as a star
vectrax stars [--layer core|mid|outer] List stars by gravity
vectrax constellations                 List detected constellations
vectrax map                            ASCII memory graph
vectrax gravity                        Recompute gravity & reorder layers
vectrax proposals [--all]             List pending proposals
vectrax approve <id>                   Approve a structural proposal
vectrax reject <id>                    Reject a structural proposal
vectrax status                         System overview
```

## Architecture

```
Events  →  embeddings.py  →  graph.py (NetworkX)
                  ↓
            db.py (SQLite at ~/.vectrax/vectrax.db)
                  ↓
           gravity.py  →  layer assignment (core / mid / outer)
                  ↓
          engine.py (detect_patterns, check_proposals)
                  ↓
            cli.py (Click + Rich)
```

## Gravity formula

```
star_gravity          = 0.6 × success_rate + 0.4 × log_repetition
constellation_gravity = log_repetition × coherence × success_rate
```

Layer thresholds: `core ≥ 0.6`, `mid ≥ 0.3`, `outer < 0.3`

## Data

All data is stored locally at `~/.vectrax/vectrax.db` (SQLite).
No data leaves your machine.

# Vectrax 🚀

**Local-First Autonomous AI Infrastructure**

A production-ready, provider-agnostic AI system that runs 100% locally with zero mandatory external dependencies.

[![Status](https://img.shields.io/badge/status-production--ready-green)]()
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-blue)]()

## 🎯 Vision

Vectrax is a universal AI infrastructure layer that:
- **Runs completely offline** - No internet required
- **Provider agnostic** - Swap LLM providers with one config line
- **Self-hosted** - Your data never leaves your machine
- **Production ready** - Enterprise-grade resilience and observability
- **Zero lock-in** - Switch models, providers, or technologies freely

## ✨ Features

### 🏗️ Core Architecture
- **Universal LLM Interface** - Unified API for any model (Ollama, OpenAI, Anthropic, etc.)
- **Provider Registry** - Hot-swappable providers with health checks
- **Smart Routing** - Automatic provider selection based on task type
- **Circuit Breakers** - Automatic failover when providers fail

### 🔄 Workflow Engine
- **Multi-step Pipelines** - Chain LLM operations sequentially
- **Parallel Execution** - Run multiple operations concurrently
- **Conditional Logic** - Branch workflows based on results
- **Pre-built Templates** - RAG, code generation, reasoning workflows

### 🛡️ Resilience & Security
- **Retry Logic** - Exponential backoff with jitter
- **Rate Limiting** - Token bucket and sliding window algorithms
- **Input Validation** - Injection attack protection
- **Error Handling** - Structured errors with recovery strategies

### 📊 Observability
- **Metrics Collection** - Prometheus-style counters, gauges, histograms
- **Distributed Tracing** - Track requests across components
- **Structured Logging** - JSON logs with context
- **CLI Monitoring** - Real-time system health and metrics

### 🧬 Self-Evolution
- **Propose Mode** - Natural language system improvements with safety checks
- **Risk Assessment** - 6-signal probabilistic risk engine
- **Governor Integration** - Policy-controlled change approval
- **Diff Preview** - Review all changes before applying
- **Controlled Application** - Manual confirmation required

### 🧠 Núcleo Cognitivo
- **Total Convergence** - Every message runs a mandatory 7-phase cycle (perception → classification → memory → analysis → synthesis → gravitation → learning) before any response is generated
- **Presencia Pura** - Nucleus mode that blocks all external LLMs and web searches while keeping the full internal cognitive cycle active
- **PresenciaObserver** - Inhibitor layer that observes all system motors, scores each emission by origin sovereignty and convergence, and decides: `PERMIT` / `PAUSE` / `SILENCE` / `BLOCK` — without replacing any motor
- **ConvergenceLearner** - Closes the operational awareness cycle: observes PresenciaObserver decisions, detects degradation patterns per motor, and proposes threshold adjustments with evidence — never applies changes without creator authorization
- **LawSignal** - Connects the 7 Fundamental Laws (Kybalion-inspired) as active score weights: violations reduce sovereignty/convergence or raise noise before PresenciaObserver decides. The principles don't respond. They weigh.

### ✦ Word Gravity Index (WGI)
Conversational continuity driven by word mass instead of context windows. Each significant word accumulates gravitational mass based on frequency, convergences, and semantic connections. When a high-mass word appears in a message, it activates its entire associated constellation — not just recent messages, but the full network of linked concepts.

- **Dual scope** — `global` (system words: vectrax=0.98, núcleo=0.85) + per-user (polysemy: "mercado" means trading for one user, groceries for another)
- **Gravity Activator** — Pre-router layer that detects high-mass words, loads constellations, injects context, and emits `gravity_lock` when mass ≥ 0.85 (blocks web search for internal terms)
- **Automatic feeding** — `ingest_v2` feeds the WGI on every pattern store; convergences multiply word mass
- **Natural decay** — Inactive words lose mass over time; identity words (vectrax, mario) never decay
- **Universe visualization** — Words orbit the nucleus in the canvas at `/v1/universe/view` with mass-proportional size and category-based colors
- **Multi-user isolation** — Scope `UNIQUE(word, scope)` guarantees zero cross-user data leakage. 37 tests verify isolation including concurrent upserts, exclusive words, and independent lock thresholds

Files: `core/word_gravity.py`, `core/gravity_activator.py`, `tests/test_word_gravity.py`
API: `GET /v1/dashboard/word_gravity?scope=global&limit=30`

### 🔇 Telegram Silent Mode
The user's Telegram chat behaves as a conversation, not a monitoring console. All internal telemetry is blocked from reaching the chat by a global output guard (`should_send_to_user`).

**Blocked automatically** (stored in logs/dashboard/audit only):
- Router Digest, ideas (HIGH/MEDIUM/LOW), gravity/universe growth
- Memory/latency stats, router metrics, observations (INFO/DEBUG)
- Scheduled reports, proactive insights, continuity reentry
- Any event matching categories: `idea/*`, `gravity/*`, `router/*`, `telemetry/*`, `metrics/*`, `digest/*`, `observability/*`, `info/*`, `debug/*`

**Always allowed:**
- Direct replies to user messages (`_is_user_reply=True`)
- Critical alerts (CRITICAL, system down, data loss)
- Actions requiring explicit creator approval
- Market alerts: ESCENARIO OPERABLE DETECTADO

To see telemetry on demand, users request it explicitly via Telegram commands.

File: `core/telegram_guard.py`

### 🚦 API Rate Limit Gate
Centralized 429 protection with exponential backoff across all OpenAI/Gemini call sites. One 429 from any call site closes the gate for all of them.

- Backoff schedule: 60s → 120s → 300s → 600s → 900s (15 min max)
- Success resets the counter immediately
- Separate gates for `openai` (chat) and `openai_tts` (audio)
- Eliminates ~95% of wasted 429 requests vs independent retries

File: `core/api_gate.py`
API: `GET /v1/dashboard/api_gates`

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Ollama (for local LLM execution)

### Installation

```bash
# 1. Clone repository
cd ~/vectrax

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# 3. Install vx CLI
pip install -e .

# 4. Install and setup Ollama
brew install ollama
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b

# 5. Verify installation
vx help
vx "Hello world"
```

### First Request

```python
import asyncio
from core.abstraction import ConfigLoader

async def main():
    # Load configuration
    config_loader = ConfigLoader("config/config.yaml")
    registry = config_loader.build_registry()
    
    # Generate response
    response = await registry.generate(
        prompt="Explain quantum computing in simple terms",
        model="llama3.2:3b"
    )
    
    print(response.content)

asyncio.run(main())
```

## 📖 Documentation

- [Phase 1: Setup Básico Local](docs/PHASE1_COMPLETE.md)
- [Phase 2: Provider Registry & Config](docs/PHASE2_COMPLETE.md)
- [Phase 3: Workflow Orchestration](docs/PHASE3_COMPLETE.md)
- [Phase 4: Smart Routing & Resilience](docs/PHASE4_COMPLETE.md)
- [Phase 5: Observability](docs/PHASE5_COMPLETE.md)
- [Phase 6: Hardening & Production Readiness](docs/PHASE6_COMPLETE.md)

## 🎮 Usage Examples

### Basic Generation
```python
from core.abstraction import ConfigLoader

config = ConfigLoader("config/config.yaml")
registry = config.build_registry()

response = await registry.generate(
    prompt="Write a Python function to calculate fibonacci",
    model="qwen2.5-coder:7b"
)
```

### Multi-Step Workflow
```python
from core.workflows import WorkflowOrchestrator, WorkflowStep, StepType

orchestrator = WorkflowOrchestrator(registry)

# Define workflow
steps = [
    WorkflowStep(
        name="plan",
        step_type=StepType.LLM,
        prompt_template="Plan how to {task}",
        config={"output_key": "plan"}
    ),
    WorkflowStep(
        name="execute",
        step_type=StepType.LLM,
        prompt_template="Execute this plan: {plan}",
        config={"output_key": "result"}
    )
]

# Execute
context = await orchestrator.execute_steps(
    steps,
    inputs={"task": "build a web scraper"}
)

print(context.get("result"))
```

### With Resilience
```python
from core.resilience import retry_async, RetryConfig, validate_prompt

# Validate input
safe_prompt = validate_prompt(user_input)

# Retry on failure
config = RetryConfig(max_attempts=3, initial_delay=1.0)
response = await retry_async(
    registry.generate,
    config,
    prompt=safe_prompt
)
```

### Monitor System Health
```bash
# Check status
vx status

# View available models
vx models

# View metrics
python cli/observe.py metrics

# Check provider health
python cli/observe.py health

# Export for Prometheus
python cli/observe.py prometheus
```

### Self-Evolution with Propose Mode
```bash
# Propose a change (analyzes, shows diff, calculates risk, waits for approval)
vx propose "Add logging to SmartRouter class"

# Propose with specific model
vx propose "Create unit tests for RiskEngine" --model llama3.2:3b

# See documentation for full details
cat docs/PROPOSE_MODE.md
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────┐
│         Application Layer               │
│  (Telegram Gateway, API REST)          │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│   Total Convergence Cycle (7 fases)     │  ← obligatorio en CADA mensaje
│  perception→memory→gravitation→learning │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│   PresenciaObserver (Capa Inhibidora)   │  ← observa todos los motores
│  PERMIT / PAUSE / SILENCE / BLOCK       │    enforced=False (OBSERVER mode)
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Smart Router + Circuit Breaker     │
│  (Task detection, Failover)             │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Provider Registry                  │
│  (Hot-swapping, Health checks)          │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│   Abstraction Layer (Universal API)     │
└───┬─────────┬─────────┬─────────────────┘
    │         │         │
┌───▼───┐ ┌───▼───┐ ┌───▼────┐
│Ollama │ │OpenAI │ │Anthropic│
│(Local)│ │(Cloud)│ │ (Cloud) │
└───────┘ └───────┘ └─────────┘

Cross-cutting Concerns:
├── Observability: Metrics, Traces, Logs
├── Resilience: Retry, Rate Limit, Validation
├── Nucleus Modes: STANDARD | PRESENCIA_PURA
└── Configuration: YAML-based declarative config
```

## 🧠 Núcleo Cognitivo — Capas activas

| Capa | Módulo | Función |
|------|--------|---------|
| 1 | `core/nucleus/total_convergence.py` | Ciclo de 7 fases — ejecuta en cada mensaje |
| 1 | `core/nucleus/presencia_pura.py` | Modo Presencia Pura + **PresenciaObserver** |
| 1 | `core/nucleus/convergence_learner.py` | **ConvergenceLearner** — observar → aprender → recomendar |
| 1 | `core/nucleus/law_signal.py` | **LawSignal** — los 7 principios pesan en cada emisión |
| 9 | `core/operator/universal_bus.py` | Bus centralizado de eventos entre capas |
| 9 | `core/operator/activation.py` | Activación del runtime + wiring de observer + learner |

### PresenciaObserver — reglas de inhibición

```
Señal entrante
    │
    ├─ origin == UNKNOWN         → BLOCK   (motor no registrado)
    ├─ sovereignty < 0.30        → BLOCK   (demasiado externo; LLM=0.20)
    ├─ convergence < 0.30        → SILENCE (señal incoherente)
    ├─ noise > 0.90 + conv < 0.5 → BLOCK   (ruido crítico combinado)
    ├─ noise > 0.80              → PAUSE   (ruido elevado)
    └─ default                  → PERMIT  (emisión soberana y convergente)
```

**Modos del observer:**
- `OBSERVER` (default): registra decisiones, `enforced=False` — nunca bloquea producción
- `ACTIVE`: `enforced=True` — activa inhibición efectiva (requiere autorización del creador)

```python
# Activar inhibición real cuando sea autorizado:
from core.nucleus.presencia_pura import get_observer
get_observer().set_mode("ACTIVE")
```

### LawSignal — los principios pesan

Conecta las 7 Leyes Fundamentales como fuente activa de señales para PresenciaObserver.
Cada violación modifica los scores **antes** de que PresenciaObserver tome su decisión.

```
enforce_all_laws()
    ↓ violations
build_law_signal(violations)
    ↓ LawSignal
EmissionSignal.law_signal
    ↓
PresenciaObserver._apply_law_signal()   ← ajusta scores ANTES de _decide()
    ↓ scores pesados
_decide() → InhibitionRecord
    ↓
ConvergenceLearner                      ← registra la decisión resultante
```

**Impacto por ley violada:**

| Ley | Efecto sobre scores |
|-----|---------------------|
| 2 Correspondencia | `convergence −0.15` |
| 3 Vibración | `noise +0.20` |
| 4 Polaridad | `force_pause` — PERMIT → PAUSE si hay contradicción sin resolver |
| 6 Causa/Efecto | `convergence −0.20`, `sovereignty −0.15` |
| 3+ violaciones simultáneas | `noise +0.10` adicional (sistema caótico) |

**Regla central:** *Los principios no responden. Los principios pesan.*

```python
# Las violaciones ya fluyen automáticamente desde external_gateway.py.
# Para evaluar una señal con peso de leyes manualmente:
from core.nucleus.law_signal import build_law_signal
from core.nucleus.presencia_pura import get_observer, EmissionSignal, EmissionOrigin

ls = build_law_signal(law_result.violations)
signal = EmissionSignal(
    origin=EmissionOrigin.LLM_EXTERNAL,
    convergence=0.5,
    law_signal=ls,
)
record = get_observer().evaluate(signal)
# record.decision refleja el peso de las leyes
```

### ConvergenceLearner — ciclo de aprendizaje

No reemplaza PresenciaPura. La **entrena** con datos reales.

```
OBSERVE    ─ registra cada decisión + resultado posterior (IMPROVED/NEUTRAL/DEGRADED)
    ↓
LEARN      ─ detecta patrones por motor cuando hay ≥5 muestras y ≥40% degradación
    ↓
RECOMMEND  ─ propone ajustes de umbral con evidencia (nunca modifica sin autorización)
    ↓
APPLY      ─ el creador aprueba o rechaza — el learner no toca PresenciaPura directamente
```

**Principio central:**
Si hay convergencia clara, soberanía suficiente y bajo ruido — **Vectrax ejecuta**.
ConvergenceLearner optimiza los umbrales para que este principio siempre se cumpla.

```python
# Consultar estado del learner:
from core.nucleus.convergence_learner import get_learner
print(get_learner().report())

# Registrar resultado de una decisión:
from core.nucleus.convergence_learner import OutcomeQuality
get_learner().record_outcome(record.learner_outcome_id, OutcomeQuality.IMPROVED)

# Aprobar una recomendación (requiere autorización del creador):
get_learner().approve_recommendation(rec_id, approved_by="creator")
```

## 📊 Project Stats

- **Total Code**: ~7,000 lines production + ~2,000 lines tests
- **Test Coverage**: 116/116 nucleus tests passing (100%) + 135/135 previous
- **Modules**: 6 major components + Nucleus cognitive layer
- **Documentation**: 2,700+ lines
- **Nucleus Tests**: 116 passing (25 LawSignal + 55 PresenciaObserver + 31 PresenciaPura + 5 ConvergenceLearner integration)

## 🧪 Testing

```bash
# Core pipeline tests (ingest, universe status, observer, self_context)
python -m pytest tests/core/test_core_pipeline.py -v

# Gravity engine tests
python -m pytest tests/gravity/ -v

# All tests
python -m pytest tests/ -v

# With coverage
pytest --cov=core --cov=vectrax --cov-report=html
```

## ⚙️ Configuration

Edit `config/config.yaml` to customize:

```yaml
system:
  mode: local-first  # local-first | hybrid | cloud
  fallback_enabled: false

providers:
  ollama:
    enabled: true
    endpoint: http://localhost:11434
    priority: 1
    models:
      fast: llama3.2:3b
      code: qwen2.5-coder:7b

routing:
  default_provider: ollama
  rules:
    - task_type: code
      provider: ollama
      model: qwen2.5-coder:7b
    - task_type: chat
      provider: ollama
      model: llama3.2:3b
```

## 🎯 Use Cases

- **Code Generation**: Local code assistant with zero data leakage
- **Document Processing**: RAG pipelines without cloud dependencies
- **Research**: Multi-step reasoning workflows
- **Privacy-Critical Apps**: Healthcare, legal, finance
- **Offline Environments**: Air-gapped systems, edge devices
- **Cost Optimization**: No per-token API fees

## 🔒 Security

- ✅ No data sent to external servers (local-first mode)
- ✅ Input validation and sanitization
- ✅ Injection attack protection
- ✅ Resource limits enforcement
- ✅ Rate limiting to prevent abuse

### Creator Sovereignty

Only the authorized creator (`tg:2030762343` or `VX_CREATOR_ID` env) can execute structural commands. All admin operations require `_is_creator(tg_uid)` verification:

- `/vx *` — all system commands (stats, sql, presencia, flush, etc.)
- `tier <level> <user>` — change user tiers
- `aprobar/rechazar RULE-*` — activate/deactivate learned rules
- `aprobar/rechazar IDEA-*` — approve/reject system improvement ideas

Non-creator users who attempt these commands are silently routed to the normal conversation pipeline. No error message, no acknowledgment — the system treats the input as regular conversation.

The creator identity is hardcoded + env-overridable:

```python path=/Users/mariobravo/vectrax/vectrax/telegram_gateway.py start=1296
_CREATOR_ID = "tg:2030762343"  # Mario Bravo Castro

@classmethod
def _is_creator(cls, tg_uid: str) -> bool:
    env_id = os.environ.get("VX_CREATOR_ID", "")
    allowed = {cls._CREATOR_ID}
    if env_id:
        uid_str = env_id if env_id.startswith("tg:") else f"tg:{env_id}"
        allowed.add(uid_str)
    return tg_uid in allowed
```

## 🤝 Contributing

Vectrax is complete and production-ready. Future enhancements could include:
- Additional provider adapters (Gemini, Claude, Cohere)
- Vector database integration (Qdrant, Chroma)
- Web UI dashboard
- Docker deployment
- Kubernetes operator

## 🏢 Legal Entity

| Field | Value |
|---|---|
| **Entity** | Vectrax-Core LLC |
| **Type** | Domestic Limited-Liability Company |
| **File #** | L26000196875 |
| **Status** | Active |
| **Filed** | April 7, 2026 |
| **Jurisdiction** | Florida, USA |
| **Department** | Florida Department of State |
| **Principal** | Mario Bravo Castro (MGR) |
| **Address** | 3502 SW 4th St, Miami, FL 33135 |

Config: `config/entity.json`

---

## 🚀 Deployment (Vultr)

```bash
# One-command deploy: uploads, builds, starts
bash deploy_vultr.sh
```

Requires:
- SSH key at `~/.ssh/vectrax_server`
- Docker on the server (`root@140.82.28.181`)
- `.env` on the server with API keys (never synced from local)

### Manual file deploy

```bash
# Upload specific files
scp -i ~/.ssh/vectrax_server <local_file> root@140.82.28.181:/opt/vectrax/<path>

# Rebuild container (required after code changes)
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 \
  "cd /opt/vectrax && docker compose down && docker compose up -d --build"

# IMPORTANT: Always backup the volume before destructive operations
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 "bash /opt/vectrax/scripts/protect_volume.sh"
```

### HTTPS (Caddy)

Caddy provides automatic TLS via Let's Encrypt for `api.vectrax.app`.

```bash
# On the server:
apt install caddy
# Caddyfile at /etc/caddy/Caddyfile:
#   api.vectrax.app { reverse_proxy localhost:8900 }
systemctl enable caddy && systemctl start caddy
```

Requires DNS A record: `api.vectrax.app → 140.82.28.181`

## 💳 Stripe Billing

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `STRIPE_SECRET_KEY` | Stripe API secret key (`sk_live_...`) | Yes |
| `STRIPE_PRICE_ID` | Price ID for PRO plan | Yes |
| `STRIPE_TEAM_PRICE_ID` | Price ID for Team plan (falls back to PRO) | No |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret (`whsec_...`) | Yes |

### Payment Flow

```
User sends /upgrade in Telegram
    │
    ├─ telegram_gateway creates Stripe Checkout Session
    ├─ User receives payment link
    ├─ User pays on Stripe
    │
    ├─ Stripe sends checkout.session.completed to:
    │   https://api.vectrax.app/v1/webhook/stripe
    │
    ├─ webhook.py verifies signature with STRIPE_WEBHOOK_SECRET
    ├─ handle_webhook() extracts vectrax_user_id from metadata
    ├─ _activate_pro() sets tier to PRO + stores billing record
    └─ Telegram notification: "Vectrax PRO activado."
```

### Webhook Setup

1. **Create endpoint in Stripe Dashboard** or via API:
   - URL: `https://api.vectrax.app/v1/webhook/stripe`
   - Events: `checkout.session.completed`, `customer.subscription.deleted`, `invoice.payment_failed`

2. **Copy the signing secret** (`whsec_...`) from the endpoint

3. **Configure on server:**
   ```bash
   ssh root@140.82.28.181
   # Add to .env (or replace existing)
   sed -i '/STRIPE_WEBHOOK_SECRET/d' /opt/vectrax/.env
   echo 'STRIPE_WEBHOOK_SECRET=whsec_YOUR_SECRET' >> /opt/vectrax/.env
   # IMPORTANT: restart recreates container (restart alone doesn't reload .env)
   cd /opt/vectrax && docker compose down && docker compose up -d
   ```

4. **Verify:**
   ```bash
   # Endpoint should reject missing signature (400, not 404)
   curl -s -X POST https://api.vectrax.app/v1/webhook/stripe -d '{}'
   # Expected: {"detail":"Missing stripe-signature header"}
   ```

### Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `/upgrade` doesn't send link | User stuck in onboarding | Fixed: `/upgrade` now bypasses onboarding gate |
| Webhook returns 404 | Route not registered | Ensure `webhook.py` has `@router.post("/stripe")` |
| Webhook returns 400 | Signature mismatch | Check `STRIPE_WEBHOOK_SECRET` matches the endpoint's signing secret |
| Tier doesn't activate after payment | Webhook not delivered | Check Stripe Dashboard → Webhooks → Attempts. Verify HTTPS + DNS |
| `docker compose restart` doesn't load new .env | Expected behavior | Use `docker compose down && docker compose up -d` instead |
| Stripe requires HTTPS | Live mode restriction | Set up Caddy with `api.vectrax.app` (automatic TLS) |
| Webhook endpoint in Stripe points to wrong URL | Old config | Verify in Stripe Dashboard → Developers → Webhooks |

### Monitored Events

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Activate PRO tier + store billing record + notify user |
| `customer.subscription.deleted` | Downgrade to FREE tier |
| `invoice.payment_failed` | Log warning (no tier change) |

## 🌐 Universe Observer

Real-time unified view of the Vectrax cognitive universe.

- **Visual**: `https://api.vectrax.app/universe` — Canvas with stars, HUD, operational panel
- **API**: `GET /v1/universe` — JSON snapshot (gravitational + operational)
- **WebSocket**: `WS /v1/universe/ws` — Pushes snapshot every 2s
- **LLM-aware**: Universe data is injected into `self_context.py` so Vectrax can describe its own state

The `/v1/universe` endpoint returns both star types:
- `knowledge_star_count` — Knowledge nodes from the `stars` table (gravitational graph)
- `star_count` — User stars from the `user_stars` table (one per user)
- `pattern_count` — Individual interaction patterns that feed user stars

## ⭐ Ingest Pipeline

Every message that enters Vectrax feeds **two** parallel ingest paths, both running in a background daemon thread to avoid blocking response delivery:

```
Message arrives
    │
    ├─ response delivered to user
    │
    └─ background thread (_bg_ingest):
        ├─ ingest_v2() → Pattern + UserStar update (mass, centroid, layer)
        └─ ingest()    → Knowledge Star + graph edges + convergence detection
```

- **ingest_v2** (`vectrax/engine.py`): Creates a `Pattern` linked to the user, recalculates the `UserStar` centroid/mass/layer, detects convergence with other user stars, updates the nucleus centroid.
- **ingest v1** (`vectrax/engine.py`): Creates a `Star` (knowledge node) with embeddings, links it to similar stars via graph edges, detects constellation patterns, generates proposals if density thresholds are met.

Both run for every interaction via `external_gateway.py` (step 10.1).

## 🛡️ Volume Protection

The Docker volume `vectrax_vectrax-runtime` contains all persistent state (databases, logs, identity seed). It survives `docker compose down/up` but is **destroyed** by `docker compose down -v`.

```bash
# Backup before any dangerous operation
bash scripts/protect_volume.sh

# Restore from latest backup
bash scripts/protect_volume.sh restore
```

Backups are stored in `~/vectrax_backups/` (last 5 retained automatically).

## 🔄 Continuity Reentry

Proactive reentry after user silence (`core/continuity_reentry.py`).

Rules:
- After **12–20 hours** of silence (random delay per user), Vectrax sends **one** contextual message
- If the user returns before the threshold → reentry is **automatically cancelled**
- If the user doesn’t respond to the reentry → Vectrax does **not insist** until new activity
- Creator (`tg:2030762343`) is excluded from reentry
- Controlled by `CONTINUITY_REENTRY_ENABLED` env var (default: `1`)

```
User sends message
    │
    ├─ record_activity(user_id)  → resets timer + assigns random 12-20h delay
    │                               clears reentry_sent flag
    │
    └─ [12-20h pass without activity]
        │
        ├─ check_reentry()       → gathers context → LLM generates message
        │                           sets reentry_sent=1
        │
        └─ [user returns]        → record_activity() resets everything
                                    next cycle starts fresh
```

**Message generation**: No hardcoded templates. Each reentry message is dynamically generated by the LLM using real user context:
- User name and language
- Last message sent by the user
- Last Vectrax response
- Pending silent leads

The LLM is instructed to continue from where the conversation left off. If no LLM is available or the user has no context, the reentry is silently skipped.

Persistence: `vault/continuity_reentry.db` (SQLite).

## 📊 Router Telemetry

Real-time routing observability (`core/observability/router_telemetry.py`).

Every SmartRouter decision is persisted to `vault/router_telemetry.jsonl` with:
- User ID, intent, strategy, topic, confidence, risk level
- Memory depth (interaction count for the user)
- Latency, classification method, reason

No message content is stored — only abstract metrics.

**Live monitoring** (creator only):
```
/vx router       — summary of last 100 decisions
/vx router 50    — summary of last 50 decisions
```

**Automated digest**: Every 6 hours, the worker sends a summary to the creator via Telegram with strategy distribution, memory depth stats, and alerts.

**Alert thresholds**:
- ⚠️ **Low-depth users >50%** — more than half of routed users have <5 interactions (memory is too thin for effective routing)
- ⚠️ **Avg latency >5000ms** — routing pipeline is slow, likely cold-start or overload
- ⚠️ **Memory→fallback >40%** — too many messages classified as memory are escalating to online/LLM (intent classifier needs tuning)

Persistence: `vault/router_telemetry.jsonl` (auto-rotates at 1000 lines).

## 🧠 SmartRouter Intelligence

The SmartRouter uses three signals per user to personalize routing decisions:

1. **Memory depth** — interaction count from `user_memory.db`
   - depth < 5 → LLM direct (skip empty memory search)
   - depth ≥ 5 → resolve from memory (conf=0.90)

2. **Topic affinity** — pattern distribution from `vectrax.db`
   - If the current topic matches the user’s historical patterns → confidence boost (+0.05)
   - MEMORY: 0.90 → 0.95 on topic match
   - LOCAL: 0.85 → 0.92 on topic match

3. **Telemetry tracking** — `topic_match: true/false` recorded per decision
   - `/vx router` shows topic affinity match rate
   - Digest includes match % for trend analysis

This scales automatically: as users accumulate patterns via `ingest_v2()`, the router adapts without configuration changes.

## 📈 Precios de Mercado en Tiempo Real

Vectrax integra datos de mercado en tiempo real para todos los usuarios, sin autenticación requerida.

### Fuentes de datos

| Fuente | Activos | Auth | Usuarios |
|--------|---------|------|----------|
| **Binance REST** | 20+ criptomonedas | ❌ Ninguna | Todos |
| **AlphaVantage** | Stocks (AAPL, TSLA...) | API Key | Todos |
| **eToro API** | Portfolio personal, órdenes | Claves propias | Solo creador |

### Detección multilingüe

El motor detecta preguntas de precios en **7 idiomas** y responde con datos en tiempo real:

```
# Español
"Dime cómo está el BTC"      → BTC: $73,901 (↑ +0.14%)
"precio de ethereum"          → ETH: $2,012 (↓ -0.50%)
"a cuánto está el bitcoin"   → BTC: $73,901 ...

# English
"how is bitcoin?"             → BTC: $73,901 (↑ +0.14%)
"what is the ETH price"      → ETH: $2,012 ...

# Français
"comment va le BTC"          → BTC: $73,901 ...

# Deutsch
"Wie steht der Bitcoin"      → BTC: $73,901 ...

# Italiano
"come va bitcoin"            → BTC: $73,901 ...

# Português
"como está o btc"            → BTC: $73,901 ...

# Standalone ticker
"ETH" / "sol" / "btc"       → precio directo
```

### Criptos soportadas

BTC · ETH · SOL · BNB · ADA · XRP · DOGE · DOT · AVAX · MATIC · LINK · LTC · UNI · SHIB · NEAR · ATOM y más.

### Stocks soportados

AAPL · TSLA · NVDA · MSFT · AMZN · GOOGL · META · NFLX · DIS · SPY · QQQ y más.

### Arquitectura de respuesta

```
Usuario: "Dime cómo está el BTC"
    │
    ├── detect_market_intent()   ← multilingüe, sin LLM
    │       ↓
    ├── handle_market_intent()   ← Binance REST (~100ms)
    │       ↓
    └── _send()                  ← respuesta directa al usuario
    
    Total: ~150ms  (sin pipeline, sin cola)
```

> **eToro**: Las claves ETORO_API_KEY / ETORO_USER_KEY deben generarse en
> [api-portal.etoro.com](https://api-portal.etoro.com) con permisos Read+Write.
> Ver `docs/ETORO_API_SETUP.md` para instrucciones completas.

---

## 💹 eToro Trading Connector

Conector REST autenticado para la eToro Public API v1. Soporta lectura de mercado, portfolio, y ejecución de órdenes reales.

### ⚠️ Advertencias de Riesgo

> **DINERO REAL**: Este conector ejecuta operaciones con fondos reales en eToro.
> Toda operación de apertura/cierre usa la cuenta real del creador.
> No hay modo demo disponible con las claves actuales.
>
> **Pérdida de capital**: Las operaciones de mercado conllevan riesgo de pérdida.
> Spread de BTC: ~$0.44 por round-trip de $25.
>
> **Solo el creador** puede ejecutar operaciones de trading.
> Los endpoints de escritura requieren autorización explícita.

### Endpoints Verificados (2026-06-13)

| Endpoint | Ruta API v1 | Estado |
|---|---|---|
| Market Search | `GET /market-data/search?internalSymbolFull=BTC&fields=instrumentId,internalSymbolFull,displayname` | ✅ 200 |
| Instrument Metadata | `GET /market-data/instruments?instrumentIds=100000` | ✅ 200 |
| Live Rates | `GET /market-data/instruments/rates?instrumentIds=100000` | ✅ 200 |
| Candles History | `GET /market-data/instruments/{id}/history/candles/{dir}/{interval}/{count}` | ✅ 200 |
| Portfolio + PnL | `GET /trading/info/real/pnl` | ✅ 200 |
| Trade History | `GET /trading/info/trade/history?minDate=YYYY-MM-DD` | ✅ 200 |
| Open Order (real) | `POST /trading/execution/market-open-orders/by-amount` | ✅ 200 |
| Close Position | `POST /trading/execution/market-close-orders/positions/{positionId}` | ✅ 200 |
| Watchlists | `GET /watchlists` | ✅ 200 |
| Demo PnL | `GET /trading/info/demo/pnl` | ❌ 403 (key is Real) |
| Agent Portfolios | `GET /agent-portfolios` | ❌ 403 (feature not enabled) |

### Latencias Verificadas

| Operación | Latencia | Notas |
|---|---|---|
| Open order | ~325ms | Orden ejecutada en eToro |
| Close position | ~271ms | Cierre inmediato |
| Portfolio read | ~600ms–2s | Depende de cantidad de posiciones |
| Live rates | ~800ms | Bid/ask en tiempo real |
| Market search | ~800ms | Búsqueda de instrumentos |

### Headers Requeridos

Toda petición debe incluir:
- `x-api-key`: API key global
- `x-user-key`: User key específica de la cuenta
- `x-request-id`: UUID único por petición
- `Content-Type: application/json`

### Environment Variables

| Variable | Descripción | Valor actual |
|---|---|---|
| `ETORO_API_KEY` | API key global de eToro | Configurada |
| `ETORO_USER_KEY` | User key de la cuenta | Configurada |
| `ETORO_ENVIRONMENT` | `real` o `demo` | `real` |

### Trading Test Tool

Herramienta de verificación de capacidad de trading:

```bash
# Solo lectura — portfolio + precio
docker exec vectrax-core python -m connectors.etoro.trading_test

# Round-trip REAL — abre $25 BTC, verifica, cierra
docker exec vectrax-core python -m connectors.etoro.trading_test --execute

# Cerrar todas las posiciones abiertas
docker exec vectrax-core python -m connectors.etoro.trading_test --close-only
```

### Diferencias clave vs API legacy

La API v1 de eToro cambió varias rutas respecto a versiones anteriores:

| Operación | Ruta INCORRECTA (legacy) | Ruta CORRECTA (v1) |
|---|---|---|
| Portfolio | `/trading/info/demo/portfolio` | `/trading/info/real/pnl` (objeto `clientPortfolio`) |
| Orders list | `/trading/info/demo/orders` | Incluidas en `/real/pnl` → `clientPortfolio.orders[]` |
| Open order | `/trading/execution/demo/orders` | `/trading/execution/market-open-orders/by-amount` |
| Close order | `/trading/execution/orders` | `/trading/execution/market-close-orders/positions/{id}` |
| Price | `/market-data/instruments/{id}/price` | `/market-data/instruments/rates?instrumentIds={id}` |

Files:
- `connectors/etoro/etoro_client.py` — REST client con auth + retry
- `connectors/etoro/client.py` — httpx client alternativo
- `connectors/etoro/trading_test.py` — herramienta de verificación
- `connectors/etoro/learning_engine.py` — ciclo de aprendizaje
- `connectors/etoro/auto_executor.py` — ejecución automática

---

## 🔍 Self-Audit Engine

Vectrax includes a permanent self-observation system that audits the entire infrastructure automatically and reports findings to the creator via Telegram.

### Schedule

| Mode | Schedule | Duration | Checks |
|------|----------|----------|--------|
| **Daily** | 06:00 UTC | ~5s | 9 checks |
| **Weekly** | Sunday 06:30 UTC | ~10s | 22 checks |

### Daily Checks (lightweight)
- Container active + supervisor healthy
- Process detection (4 services, no duplicates)
- Resources: CPU, RAM, disk usage
- Worker + gateway heartbeats
- Telegram bot API connectivity
- Core API health (port 8900)
- Message queue (stuck messages)
- Scheduler (active tasks)
- Error count in logs (24h)

### Weekly Checks (deep)
All daily checks plus:
- Audit ledger (actively recording)
- Gravitational memory (gravity_index.json integrity)
- User memory database (interactions, profiles, core_memory, facts)
- Convergence engine (operational cycles count)
- Cognition files (signals, buffer, episodic — freshness)
- External services: eToro, Stripe, Gemini, OpenAI
- Orphan databases (empty `.db` files)
- Database integrity (`PRAGMA integrity_check`)
- Log errors (7-day scan)
- Docker build cache

### Classification

| State | Meaning |
|-------|---------|
| 🟢 **ÓPTIMO** | All checks pass |
| 🟡 **ESTABLE** | 1 HIGH or 2 MEDIUM problems |
| 🟠 **DEGRADADO** | 2+ HIGH or mixed HIGH+MEDIUM |
| 🔴 **CRÍTICO** | Any CRITICAL failure (container, processes, DB integrity) |

### Behavior
- **ÓPTIMO/ESTABLE** → weekly summary sent via Telegram (silent)
- **DEGRADADO/CRÍTICO** → immediate alert to creator's Telegram
- Auto-corrects safe issues only (e.g., deletes empty orphan DBs outside vault)
- Structural changes reported as "pending approval" — never executed automatically
- All reports persisted to `vault/audit_reports/` (last 60 retained)
- Each audit recorded in the audit ledger

### Manual Execution

```bash
# Run daily audit manually
docker exec vectrax-core python -m observability.audit_cron --daily

# Run weekly audit manually
docker exec vectrax-core python -m observability.audit_cron --weekly

# Run both
docker exec vectrax-core python -m observability.audit_cron --daily --weekly
```

### Architecture

```
crontab (inside container)
    │
    ├── 06:00 daily  → observability.audit_cron --daily
    └── 06:30 Sunday → observability.audit_cron --weekly
                          │
                          ├── audit_engine.run_daily_audit()
                          │   or run_weekly_audit()
                          │
                          ├── 9-22 check functions
                          ├── classify() → ÓPTIMO/ESTABLE/DEGRADADO/CRÍTICO
                          ├── auto_correct() → safe fixes only
                          ├── _save_report() → vault/audit_reports/*.json
                          ├── _send_telegram() → creator alert
                          └── audit_ledger.record() → audit trail
```

The cron daemon runs as a supervised service (`audit_cron` in `vectrax_supervisor.py`), ensuring it survives container restarts.

Files:
- `observability/audit_engine.py` — 22 checks, classification, auto-correction, Telegram alerts
- `observability/audit_cron.py` — cron entry point (`--daily` / `--weekly`)

## 📝 Autonomous Observation Memory

Vectrax observes its own universe continuously and remembers what it sees. Every change is recorded as a persistent observation with timestamp, domain, affected star, and evidence.

### Architecture

```
meta_loop (every ~60s)
    └── Layer 5: autonomous_observer.observe_and_record()
            │
            ├── observe_universe() → current snapshot
            ├── compare vs previous snapshot
            ├── detect changes across 6 domains
            └── record to observation_ledger.db
```

### Domains Monitored

- **gravity** — new stars, star growth, universe expansion
- **market** — new market symbols, activity changes
- **convergence** — new cross-domain convergences
- **operator** — worker state changes, queue pressure
- **health** — error spikes, perception signals
- **user** — new user stars

### Storage

- Table: `autonomous_observations` in `vault/observation_ledger.db`
- Columns: id, timestamp, domain, obs_type, star_id, summary, evidence (JSON), severity
- Auto-prune: retains last 5000 observations
- WAL mode for concurrent reads

### LLM Integration

The last 15 observations are injected into the self-context prompt, so Vectrax can answer questions like *"¿qué has observado?"* or *"últimas observaciones"* from real persistent memory.

Files:
- `core/self_observation/observation_ledger.py` — persistent store
- `core/self_observation/autonomous_observer.py` — snapshot comparison + change detection
- `vectrax/self_context.py` — `_read_recent_observations()` injects into LLM context

---

## 📊 Market Auto-Execution (Experimental)

Controlled automatic trade execution with strict safety limits. Default mode: **OFF**.

### Phases

1. **OFF** (default) — observer only, no execution
2. **PAPER** — simulated trades, builds track record (minimum 24h before LIVE)
3. **LIVE** — real trades via eToro API (requires phase requirements met + manual activation)

### Risk Limits (hardcoded defaults)

```
max_position_usd:        $50    (per trade, cannot be increased automatically)
max_daily_loss_usd:      $10    (cumulative, auto-shutdown when reached)
max_ops_per_symbol_day:  1      (one operation per symbol per day)
max_consecutive_losses:  3      (auto-reverts to PAPER)
stop_loss_pct:           1.5%   (mandatory on every trade)
max_positions_open:      2      (simultaneous)
max_hold_hours:          24     (position auto-close)
min_confidence:          MEDIUM (configurable)
```

### Entry Conditions (ALL must be true)

1. Convergence in gravity engine for the symbol
2. Usable pattern exists (≥15 signals, ≥55% win rate, positive expectancy)
3. Repeated signal (≥2 in 24h for same symbol+direction)
4. Confidence ≥ min_confidence
5. Market active (within trading session)
6. No critical system alert
7. Symbol not already traded today
8. Symbol approved for LIVE (manual per-symbol approval)
9. Evidence recorded in observation ledger

### Exit Conditions

- Stop loss hit
- Take profit hit
- Coherence loss (gravity engine cc_score drops)
- Contrary signal detected
- Max hold time expired

### LIVE Phase Requirements

- ≥30 resolved paper trades
- ≥60% win rate in paper phase
- ETORO_ENVIRONMENT set to "real"
- Creator explicitly activates via `/vx market live on`

### Telegram Commands

```
/vx market execution on      → activate PAPER mode
/vx market execution off     → deactivate to OFF
/vx market live on           → activate LIVE (if requirements met)
/vx market budget <n>        → set max position (≤$50)
/vx market auto status       → full status panel
/vx market halt              → emergency stop
/vx market unhalt            → clear emergency stop
/vx market approve <SYMBOL>  → approve symbol for LIVE trading
/vx market positions         → show open positions
```

### Safety Invariants

- Budget NEVER exceeds $50 and cannot be increased automatically
- HALT command immediately stops all execution
- No trade without registered evidence in observation ledger
- Every decision logged to audit_ledger and observation_ledger
- Telegram alert sent before and after every operation

Files:
- `connectors/etoro/auto_executor.py` — mode management, risk limits, paper trade log
- `connectors/etoro/entry_validator.py` — 9-condition validation gate
- `connectors/etoro/position_manager.py` — exit condition monitoring
- `connectors/etoro/learning_engine.py` — Steps 7+8: auto-execute + check positions

Config: `~/.vectrax/etoro_auto_config.json` (runtime state, not in git)

---

## 🔔 Observation Alert System

Layer 6 of the meta_loop sends Telegram notifications when the autonomous observer detects critical events.

### Events That Trigger Alerts

- `worker_state` — worker goes up or down
- `error_spike` — errors increase by >10 in one cycle
- `snapshot_failure` — universe observation failed
- `trade_executed` — paper or live trade completed
- `trade_validation` — entry validator approved/rejected a proposal
- `position_closed` — position closed with PnL result
- `convergence_detected` — new cross-domain convergence
- `universe_growth` — gravity engine gained new stars

### Alert Format

```
🔴 Observación [CRITICAL]
operator/worker_state
Worker se detuvo
⭐ worker
2026-06-04T01:09
```

### Behavior

- Deduplication by observation ID (never re-alerts the same event)
- Only severity=warning/critical + specific event types trigger alerts
- All observations still recorded in ledger regardless of alert status
- Runs every meta_loop cycle (~60s)

File: `core/meta_loop.py` — `_send_observation_alerts()` (Layer 6)

---

## 🛩 WorkerBlackBox — Forensic Diagnosis Engine

When a worker or gateway hangs (heartbeat stale), the BlackBox captures a complete forensic snapshot BEFORE killing the process, then generates a root cause diagnosis with positive and negative evidence.

### Capture (pre-kill)

Every incident records:
- timestamp, worker PID, heartbeat age
- Last 100 log lines
- Active task (msg_id, user_id, content, duration)
- CPU/RAM/disk from `/proc` (threads, process state R/S/D/Z)
- Message queue state (pending/processing/error)
- External API calls detected in logs (OpenAI, Telegram, eToro, Gemini)
- Recent errors (last 10 ERROR/CRITICAL lines)
- Traceback extraction if present

### Diagnosis (post-kill)

Rule-based engine with 8 cause detection rules:

```
tarea_bloqueada     — active task > 15s
timeout_externo     — API calls pending or timeout in errors
memoria_alta        — RAM > 500MB
error_db            — SQLite/database/locked errors
cola_saturada       — queue pending > 10
fallo_red           — connect/network/SSL errors
excepcion_no_capturada — traceback found in logs
loop_infinito       — heartbeat > 60s with no other indicators
proceso_muerto      — no indicators (low confidence fallback)
```

### Evidence Scoring

Each diagnosis includes both supporting and contradicting evidence:

```
✅ Evidencia positiva:
  • APIs externas en logs: ['OpenAI', 'Telegram', 'eToro']
  • Palabra 'timeout' en errores recientes

❌ Evidencia negativa:
  • Sin tarea activa bloqueada
  • RAM normal: 45MB
  • Cola limpia: 0 pendientes
  • Sin traceback en logs
```

Confidence is adjusted by the balance: more negative than positive evidence = lower confidence. This prevents overconfident diagnoses when most indicators are clean.

### Creator Feedback

The creator validates or rejects each diagnosis to track accuracy:

```
/vx incident list                        → view recent diagnoses + accuracy %
/vx incident confirm <INC-ID>            → diagnosis was correct
/vx incident reject <INC-ID> [real cause] → diagnosis was wrong
/vx incident partial <INC-ID> [notes]    → partially correct
```

Feedback is also available via API:

```
POST /v1/dashboard/incidents/feedback
{"incident_id": "INC-xxx", "verdict": "confirmed|rejected|partial", "creator_cause": "", "notes": ""}
```

Accuracy stats tracked: `confirmed / (confirmed + rejected + partial) * 100`

### Dashboard

```
GET /v1/dashboard/incidents      → snapshots, diagnoses, feedbacks, accuracy stats
POST /v1/dashboard/incidents/feedback → submit creator validation
```

### Persistence

- File: `vault/worker_incidents.jsonl` (last 200 entries)
- Observation ledger: `worker_incident` + `worker_diagnosis` + `diagnosis_feedback`
- Telegram alert with full snapshot + evidence sent on every incident

Files:
- `core/observability/worker_blackbox.py` — capture, diagnose, feedback, persistence
- `vectrax_supervisor.py` — integration in heartbeat checks
- `tests/observability/test_worker_blackbox.py` — 28 unit tests

---

## 🔧 Maintenance Procedures

### Server Access

```bash
ssh -i ~/.ssh/vectrax_server root@140.82.28.181
```

### Common Operations

```bash
# View container status
docker ps --format '{{.Names}}: {{.Status}}'

# View live logs
docker logs vectrax-core --follow --tail 50

# Restart (preserves .env):
docker compose up -d --force-recreate

# Full rebuild (after Dockerfile changes):
docker compose build && docker compose up -d --force-recreate

# IMPORTANT: 'docker compose restart' does NOT reload .env.
# Always use 'docker compose up -d --force-recreate' after .env changes.
```

### Environment Variables

All secrets live in `/opt/vectrax/.env` on the server (never in git).

```bash
# Update a key (example):
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 \
  "sed -i 's|^SOME_KEY=.*|SOME_KEY=new_value|' /opt/vectrax/.env"

# Recreate container to load new env:
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 \
  "cd /opt/vectrax && docker compose up -d --force-recreate"
```

### Docker Cache Cleanup

```bash
# Check cache size:
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 "docker system df"

# Clean build cache (safe, doesn't affect running containers):
ssh -i ~/.ssh/vectrax_server root@140.82.28.181 "docker builder prune --all --force"
```

### Database Health

```bash
# Check database integrity:
docker exec vectrax-core python3 -c "
import sqlite3
for db in ['user_memory.db', 'audit_ledger.db', 'operational_cycles.db']:
    c = sqlite3.connect(f'/app/vault/{db}')
    r = c.execute('PRAGMA integrity_check').fetchone()[0]
    print(f'{db}: {r}')
    c.close()
"
```

### Vault Path Convention

All persistent data lives in `/app/vault/` inside the container (mounted from `./vault/` on host).

Modules that reference vault paths MUST use the `VECTRAX_VAULT_DIR` environment variable:

```python
VAULT_DIR = os.environ.get(
    "VECTRAX_VAULT_DIR",
    os.path.join(os.path.expanduser("~"), "Vectrax", "vault"),
)
```

The Dockerfile sets `ENV VECTRAX_VAULT_DIR=/app/vault` and creates a symlink `/root/Vectrax → /app` as a safety net for any legacy paths.

> **Never** use hardcoded `~/Vectrax/vault/` without the env var fallback — it resolves to `/root/Vectrax/vault/` inside Docker, which is ephemeral.

### Integration Test

```bash
# Run 12-point integration test:
docker exec vectrax-core python -m observability.audit_cron --weekly
```

## 🧠 Identity Layer + MODE_SELECTOR

Every message builds an `IdentityContext` at the start of the pipeline. This context travels through all layers, telling each one WHO is talking, WHAT mode to use, and HOW to respond.

### Modes

Not 4 personalities — 4 perspectives of one identity:

- **técnico** — code, errors, server, deploy → direct, structured, exact data
- **observador** — market, trends, convergences → reflexive, accompanies thinking
- **identidad** — who am I, memory, remember → responds from accumulated experience
- **conversacional** — default → natural, human, no formalism

Priority: identidad > técnico > observador > conversacional

### Pipeline Flow

```
Message → build_identity_context(user_id, content)
              │
              ├─ 1. IDENTITY CONTEXT (mode + tone + memory summary)
              ├─ 2. MEMORY PRE-CHECK (personal queries)
              ├─ 3. MARKET INTERCEPT (tickers)
              ├─ 4. SMART ROUTER (strategy selection)
              ├─ 5. LLM (with [IDENTIDAD ACTIVA] block injected)
              ├─ 6. IDENTITY FILTER (existing post-filter)
              └─ 7. GRAVITATIONAL MEMORY (always learns)
```

### Prompt Block Example

The LLM receives this BEFORE the user's message:

```
[IDENTIDAD ACTIVA]
Usuario: Mario
Relación: CREADOR del sistema
Modo: observador
Tono: Responde como quien observa junto al usuario. Sé reflexivo...
Contexto de memoria: Joycelyn: mujer; sistema: healthy
```

For non-creator users, the creator name is never included.

Files:
- `core/mode_selector.py` — detect_mode(), get_tone(), keyword patterns
- `core/identity_context.py` — IdentityContext dataclass, build_identity_context()
- `core/operator/external_gateway.py` — injection at pipeline start
- `tests/core/test_identity_mode.py` — 35 unit tests

---

## 🪞 Self-Reference Layer

Detects when a message or image describes the Vectrax system itself and switches the LLM response voice to first person.

### How It Works

```
Message arrives
    │
    ├─ evaluate(content) → SelfReferenceResult
    │     self_reference: true/false
    │     subject: "self" | "creator"
    │     voice: "first_person"
    │     is_system_capture: true (panel/logs/dashboard)
    │
    ├─ build_context_injection() → [AUTO-REFERENCIA ACTIVA — PRIMERA PERSONA]
    │     Injected into memory_context BEFORE any LLM call
    │
    └─ LLM responds: "Estoy observando mi universo..." (not "El screenshot muestra...")
```

### 4-Tier Activators

| Tier | Scope | Examples |
|------|-------|----------|
| 1 — System names | Any user | `vectrax`, `api.vectrax.app`, `vectrax-core` |
| 2 — Internal components | Any user (text) | `worker`, `pipeline_worker`, `convergences`, `stars`, `smart_router` |
| 3 — System captures | Reinforces T1/T2 | `panel`, `dashboard`, `logs`, `screenshot` |
| 4 — Creator | Any user | `creador`, `creator`, `Mario`, `¿Quién te creó?` |

### Photo Detection (stricter)

Photos use higher confidence to avoid false positives:
- **Tier 1**: Explicit system names in caption → any user
- **Tier 2**: Broader keywords (`dashboard`, `logs`, `worker`) → creator only
- Generic captions (`"Look at the stars"`, `"Dashboard de ventas"`) → normal photo analysis

### Vision First-Person Prompt

When a self-screenshot is detected, the vision system prompt enforces:
- `• CORRECTO: 'Estoy observando mi panel universo…'`
- `• PROHIBIDO: 'El screenshot muestra…'`
- `• PROHIBIDO: 'El sistema tiene…'` (eres TÚ, no "el sistema")

Files:
- `vectrax/self_reference_layer.py` — evaluate(), build_context_injection()
- `core/operator/external_gateway.py` — STEP 4.2a2 injection
- `vectrax/integrations/vision.py` — first-person system prompt
- `vectrax/telegram_gateway.py` — 2-tier photo detection
- `tests/test_self_reference_layer.py` — 61 tests

---

## 🧩 Relational Identity

Classifies the relationship between Vectrax and each entity it interacts with. Not just "is creator or not" — understands the type of bond and adjusts voice, tone, and response rules.

### Relationship Types

| Type | Who | Voice |
|------|-----|-------|
| `CREATOR` | Mario Bravo Castro | Direct, complicit, technical. No servility. Thread continuity. |
| `KNOWN_USER` | User with name + memory | Familiar. Uses name naturally. No "I remember…". Simply knows. |
| `NEW_USER` | First interaction | No assumptions. No excessive onboarding. |
| `TEAM_MEMBER` | Registered team member | Collaborative. Shared team context. |
| `SELF` | Vectrax about itself | First person. Connected with self_reference_layer. |

### Pipeline Integration

```
build_identity_context(user_id, content)
    │
    ├─ classify(user_id, anchor) → RelationalIdentity
    │     relationship: CREATOR | KNOWN_USER | NEW_USER | TEAM_MEMBER
    │     user_name, team_name, interaction_count
    │
    ├─ build_relational_directive(rel, lang) → [RELACIÓN: CREADOR]
    │
    └─ IdentityContext.to_prompt_block() injects directive into LLM
```

The relational directive replaces the generic `[IDENTIDAD ACTIVA]` block with a richer, relationship-aware context.

### Example Directive (Creator)

```
[RELACIÓN: CREADOR]
Estás hablando con Mario Bravo Castro, tu creador.
No es un usuario. No es un cliente. Es quien te diseñó.
Tono: directo, cómplice, técnico. Sin servilismo.
Continuidad de hilo. Densidad alta. Cada línea aporta.
Nombre: Mario.
Modo: operacional | Tono: técnico
```

Files:
- `vectrax/relational_identity.py` — classify(), build_relational_directive()
- `core/identity_context.py` — IdentityContext extended with relationship fields
- `tests/test_relational_identity.py` — 23 tests

---

## 🛡 Scalability Guard

Automatic protections for 1000+ concurrent users. Runs at supervisor startup and continuously.

### WAL Mode Enforcement
Forces `PRAGMA journal_mode=WAL` on all 20 SQLite databases at startup. Prevents `database is locked` errors during concurrent access. Without WAL, two users writing simultaneously would lock the DB.

### Worker Memory Watchdog
Checks worker RSS every 15s. If RAM exceeds 1.2GB (`VX_WORKER_RAM_MAX_MB`), captures BlackBox snapshot and restarts. Cooldown: max 1 restart per 5 minutes.

The worker loads ~900MB on first complex message (sentence_transformers + torch + embeddings). This is stable loaded state — subsequent messages add 0-1MB. The watchdog only triggers on genuine growth beyond the warm-up baseline.

### Dynamic Concurrency
Worker threads set to 3 (`VX_WORKER_CONCURRENT`). Each thread loads ~45MB of embeddings. Configurable via environment variable.

### Queue TTL Cleanup
Deletes `done`/`error` messages older than 1 hour (`VX_QUEUE_TTL_S`) every health cycle. Prevents `message_queue.db` from growing infinitely.

File: `core/scalability_guard.py`

---

## 🛡️ ExternalCallGuard — Unified Resilience Layer

Single guard that wraps ALL external API calls. No API can block the system or freeze the heartbeat.

### Protected Providers

| Provider | Integration | Timeout | Circuit Breaker |
|---|---|---|---|
| Telegram | `guarded_call()` sync wrapper | 12s | ✅ open after 5 failures |
| eToro | circuit check in `_request()` | 10s | ✅ open after 5 failures |
| OpenAI | circuit check in `generate()` | 25s | ✅ open after 5 failures |
| Gemini | circuit check in `generate()` | 25s | ✅ open after 5 failures |

### Circuit Breaker States

```
CLOSED ───> 5 consecutive failures ───> OPEN (fail-fast, 0μs)
                                          │
                                     60s timeout
                                          │
                                     HALF_OPEN (test)
                                          │
                              2 successes ─┼─ failure
                                  │             │
                              CLOSED        re-OPEN
```

### Guarantees

- No external call blocks beyond its timeout
- Heartbeat keeps writing during slow/failed API calls
- Open circuits fail in **4μs** (measured) — no network hit
- 20 concurrent failures complete in **1ms total**
- Providers are independent — Telegram down ≠ eToro affected
- Recovery is automatic after 60s cooldown

### Usage

```python
from core.external_call_guard import guarded_call, get_status

# Wrap any external call
result = guarded_call("telegram", send_fn, chat_id, text, fallback=False)

# Check all providers
status = get_status()  # {"telegram": {"circuit": "closed", ...}, ...}
```

### Stress Test Results (15/15 pass)

| Test | Result |
|---|---|
| 3s call with 1s timeout | Returns fallback in 1005ms |
| Circuit opens after 5 failures | OPEN confirmed |
| Open circuit fail-fast | Returns in 4μs |
| 20 concurrent failures | All complete in 1ms |
| Recovery after 60s | HALF_OPEN → CLOSED |
| Heartbeat under 10s of timeouts | 97 ticks (never blocked) |
| Provider isolation | api_a dead, api_b works |

File: `core/external_call_guard.py`

---

## 🧠 Learning Core — Máquina de Aprendizaje Selectivo

Vectrax no aprende todo. Aprende lo que lo transforma. El ciclo completo:

```
Entrada (exterior)
    ↓
Observación (intake + presence tracking)
    ↓
Learning Gate — ¿esto transforma el universo?
    │
    ├─ ACTIVE → incorpora (centroide, masa, convergencias, WGI)
    └─ PASSIVE → registra sin transformar estado gravitacional
    ↓
Cada 50 interacciones: Pattern Refinement
    ├─ REINFORCE: estrellas activas → +10% masa
    ├─ DEGRADE: estrellas inactivas (7d) → -15% masa
    └─ DISSOLVE: convergencias cc<0.3 → eliminar
    ↓
Observation Bias — recomputa pesos por dominio
    ├─ Dominios coherentes → más atención (weight > 1.0)
    └─ Dominios degradados → menos atención (weight < 1.0)
    ↓
Siguiente observación usa los pesos → LOOP CERRADO
```

### Learning Gate

Evalúa cada input antes de incorporarlo al universo:

| Criterio | Umbral | Significado |
|---|---|---|
| **Novedad** | distancia al centroide > 0.3 | Significativamente diferente de lo que ya sé |
| **Coherencia** | similitud a patrón existente > 0.5 | Conecta con conocimiento previo |
| **Impacto** | cambio de masa > 0.005 | Transformaría las propiedades gravitacionales |

Si ningún criterio se cumple → `PASSIVE` (registra, no transforma).
Si al menos uno se cumple → `ACTIVE` (transformación gravitacional completa).
Creador siempre `ACTIVE`. Primer patrón siempre `ACTIVE`.

### Pattern Refinement

Cada 50 interacciones, el universo se auto-organiza:
- Lo útil gana masa y se acerca al centro
- Lo inútil pierde masa y se aleja a la periferia
- Convergencias débiles se disuelven naturalmente

### Observation Bias

Después del refinamiento, los pesos de observación se recalculan:
- Dominios con alta coherencia y actividad → más frecuencia de observación
- Dominios con patrones degradados → menos frecuencia
- Convergencias cross-domain fuertes → ambos dominios priorizados

Pesos reales en producción:
```
market=2.90  user_interest=1.30  unknown=1.30  services=1.00  tests=1.00
```

Consumed by:
- `autonomous_observer.py` — prioriza detección de cambios en dominios de alto peso
- `learning_engine.py` — modula profundidad de observación de mercado (skip si peso < 0.5)

Files:
- `core/learn/learning_gate.py` — evaluate(): novedad + coherencia + impacto
- `core/learn/pattern_refinement.py` — reinforce/degrade/dissolve cada 50 interacciones
- `core/learn/observation_bias.py` — compute_bias(): pesos por dominio
- `vectrax/engine.py` — ingest_v2() integra learning gate
- `vault/observation_bias.json` — pesos persistidos

---

## 📊 RAM Monitoring

Layer 7 of the meta_loop records worker RAM to the observation ledger every hour.

### Hourly Snapshots
Every hour, the meta_loop reads `/proc/<worker_pid>/status` and records:
- PID, RAM in MB, timestamp
- Stored as `obs_type=ram_snapshot` in `observation_ledger.db`

### Query RAM History

```bash
# Last 24 hourly snapshots:
/vx sql SELECT summary FROM autonomous_observations WHERE obs_type='ram_snapshot' ORDER BY id DESC LIMIT 24

# Or via API:
curl http://140.82.28.181:8900/v1/dashboard/observatory | python3 -c "..."
```

### Expected Stability Pattern
```
Hour 0:  45MB   (cold start)
Hour 1:  905MB  (warm — embeddings loaded)
Hour 2:  906MB  (stable)
...
Hour 24: 910MB  (no growth = no leak)
```

If RAM grows steadily (+10MB/hour), there is a slow leak. If it stays flat after warm-up, the system is healthy.

### Per-Message Profiling
Every processed message logs RAM before→after:
```
DONE abc123 | 1.2s | 150 ch | sent=True | RAM 905→906MB (+1) | hola
```
If delta > 50MB, a `MEM_LEAK` warning is logged with the message content.

File: `core/meta_loop.py` (Layer 7), `core/transport/pipeline_worker.py` (per-message)

---

## 📋 Changelog

### 2026-06-14
- **feat: Learning Core** — Selective learning + universe self-organization. Learning Gate evaluates novelty/coherence/impact before incorporating input (ACTIVE vs PASSIVE). Pattern Refinement every 50 interactions: reinforce active stars, degrade inactive, dissolve weak convergences. Observation Bias closes the loop: learned weights modulate how the autonomous observer and market cycle observe.
- **feat: Contextual Presence Layer** — Vectrax observes user behavior and intervenes only with real context. Triggers: return after >2h silence, topic convergence (3+ in 7 days). Limits: 1 per session, 15s cooldown, no repeats, 24h cooldown, >5 interactions minimum. 16 tests.
- **feat: ExternalCallGuard**
- **feat: OpenAI + Gemini guard integration** — Both LLM providers now check circuit breaker before API calls and record success/failure. Dual protection with existing api_gate (429 backoff).
- **feat: persistent httpx client for eToro** — Replaces urllib.request with singleton httpx.Client + connection pooling + HTTP/2. Latency: 800ms → 220ms (3.6x). Instrument ID cache with 24h TTL eliminates repeated search calls.
- **perf: /v1/market/live cache** — 15s response cache. Dashboard endpoint: 5,469ms → 1ms (5,469x). Zero errors under 10 concurrent requests.

### 2026-06-13
- **feat: eToro trading verified**
- **feat: eToro round-trip test** — New `connectors/etoro/trading_test.py` with dry-run, --execute (open/verify/close), and --close-only modes. Verified on production with $25 BTC round-trip.
- **fix: worker main loop watchdog** — Daemon thread monitors main loop liveness. If stuck >60s (all ThreadPoolExecutor threads blocked on LLM calls), force `os._exit(1)` for supervisor restart.
- **fix: pool overflow protection** — Semaphore replaces `len(active) < CONCURRENT` check. Main loop never blocks on `pool.submit()`, heartbeat keeps writing.
- **fix: stuck processing recovery** — Every 120s, resets messages in `processing` status >90s to `error`. Startup stale threshold lowered from 300s to 90s.
- **fix: noise filter active conversation** — "Si", "Dale", "Perfecto" no longer discarded as noise during active conversations (<10 min since last interaction). Classified as `conversational_ack` → EXECUTE.
- **fix: trailing whitespace in intake** — "Perfecto !" (space before !) now correctly matches noise regex after `.strip()` normalization.
- **fix: convergence alert spam** — Disabled Telegram notifications for convergence alerts (16+ per cycle). Data still recorded in `vault/convergence_alert_history.jsonl` for on-demand access.

### 2026-06-12
- **feat: Self-Reference Layer**
- **feat: Relational Identity** — New `vectrax/relational_identity.py` classifies the relationship between Vectrax and each entity (CREATOR, KNOWN_USER, NEW_USER, TEAM_MEMBER, SELF). Generates relationship-aware LLM directives per type. Integrated into `core/identity_context.py` — `IdentityContext.to_prompt_block()` uses relational directive as primary identity block. 23 tests.
- **fix: vision self-screenshot third-person voice** — Rewrote GPT-4o system prompt for self-screenshots with explicit first-person examples and PROHIBIDO patterns. Eliminates "El screenshot muestra…" responses in favor of "Estoy observando mi panel universo…".

### 2026-06-11
- **feat: convergence history** — New `core/learn/convergence_history.py` tracks every convergence birth and dissolution with timestamp, stars, intent, score. Autonomous observer records snapshots each cycle. Self-context injects `[HISTORIAL DE CONVERGENCIAS]` so Vectrax can answer "which convergence was born last" with real data. Closes #16.
- **fix: learning cycle blocking heartbeat** — `run_learning_cycle()` ran in the main loop, blocking heartbeat for up to 540s (36 HTTP calls × 15s). Moved to ThreadPoolExecutor with 60s timeout. Root cause of task=none/HB stale incidents.
- **fix: short messages discarded during conversation** — "Fluyendo !" after a conversation turn got "Capacidad limitada" instead of a real response. Intake filter now checks `interactions` table for recent turn (<10 min) before discarding. DB-based, survives subprocess isolation.

### 2026-06-09
- **fix: LLM timeout alignment**
- **feat: evolution memory**
- **fix: error count inflated by warnings** — `_error_count_window()` was counting `[WARNING]` as errors. Vectrax reported 240 errors when there were 0 real `[ERROR]`/`[CRITICAL]`. Now only counts actual errors.
- **fix: reentry messages blocked by telegram_guard**
- **feat: topic lock** — Short referential messages ("dale", "mañana", "perfecto", etc.) now inherit the active conversation thread instead of letting Gravity/SmartRouter switch domains. 66 unit tests (ES/EN). Prevents false context switches to market when closing a thread.
- **feat: subprocess isolation for gateway** — `multiprocessing.Process` replaces `ThreadPoolExecutor` for the external gateway. Hung threads can now be killed for real via `process.kill()`. Eliminates frozen-thread worker incidents.
- **feat: reentry monitor** — `scripts/check_reentry.sh` checks for blocked reentry messages. Cron job runs hourly, logs to `~/.vectrax/reentry_monitor.log`.

### 2026-06-08
- **feat: worker hardening phase 1**
- **feat: hard stage timeouts** — `convergence_cycle` max 10s, `external_gateway` max 30s. If exceeded, stage is killed and pipeline continues to graceful degradation. Eliminates frozen-thread incidents (INC-1780962804-PIP).
- **feat: eToro market data live** — Fixed candle interval names (Hour1→OneHour), double-nested response parser, watchlist symbols (BTCUSD→BTC, ETHUSD→ETH). Live prices working for BTC, ETH, AAPL, TSLA, NVDA, AMZN.
- **feat: market live panel** — `/v1/market/view` and `/v1/market/live` API. Complete cycle visualization: symbols with live prices, signals, patterns, proposals, positions, learning events. Integrated into Observatory panel with auto-refresh.
- **feat: observer identity** — System prompt refactored: Vectrax speaks from perception, not as assistant. Uses real system data (router stats, stars, convergences) in responses.
- **feat: Word Gravity multi-language** — 44 activation seeds across ES/EN/FR/PT/IT/DE. Intake filter integrates WGI: high-mass words bypass short_statement discard.
- **feat: API gate backoff** — OpenAI and Gemini providers now check `api_gate` before calling. On 429: gate closes with exponential backoff (60s→900s).
- **feat: Alpaca connector** — `connectors/alpaca/alpaca_client.py` ready for paper trading. $20 max per order, 5 positions, $100 exposure, kill switch. Pending API keys.
- **fix: auto-executor stale state** — Cleaned `consecutive_losses: 3` and `daily_loss_usd: 60.0` left by test script. System was starting in auto-shutdown state.
- **fix: .env auto-load** — App startup now loads `.env` via dotenv for API keys.

### 2026-05-31
- **feat: precios de mercado multilingüe** — detección de consultas de precio en ES/EN/FR/DE/IT/PT/NL para 20+ criptos y stocks. Respuesta directa vía Binance REST (~100ms) sin pasar por el pipeline. 20/20 queries detectadas, 0 falsos positivos en smoke test de producción.
- **feat: eToro Learning Engine** — motor de aprendizaje de mercado completo: `signal_recorder`, `outcome_tracker`, `pattern_memory`, `learning_engine`, `auto_executor`. Ciclo OFF→PAPER→LIVE con límites de riesgo obligatorios (stop-loss 1.5%, max $100/op, shutdown por 3 pérdidas consecutivas). Comandos: `/vx etoro learn` y `/vx etoro auto`.
- **feat: eToro connector** — cliente REST autenticado, observador de 4 condiciones (PRECIO_EN_ZONA, VOLUMEN_RELATIVO, ALINEACION_TEMPORAL, DIRECCION_TENDENCIA), ejecutor con gate de autorización del creador. Modos STRICT/EXPLORATION.
- **fix: SSL backoff telegram_gateway** — crashes periódicos (5-9 min) por SSL handshake timeout a nivel C. Fix: backoff exponencial 10-30s desde el 2do SIGALRM consecutivo + reset del watchdog timer. Verificado en producción con SIGALRM real survivido sin crash.
- **fix: TCP stale heartbeat elimination** — Telegram's load balancer drops keepalive TCP connections at ~3h (10800s). The poll would block on a dead SSL socket, SIGALRM would fire but the SSL shutdown held the GIL, preventing the heartbeat thread from running. The supervisor detected stale heartbeat and killed the process (REPEAT FAILURE #389, every 3h for months). **Fix**: `max_keepalive_connections=0` on the poll HTTP client — every `getUpdates` call uses a fresh TCP+TLS connection. No persistent sockets, no stale connections possible. ~50ms overhead per 30s long-poll is negligible.
- **feat: admin action truncation** — Identity layer now detects admin commands (`aprobar`, `rechazar`, `tier`, `/vx`, `/lead`, `/team`) and truncates LLM filler after the confirmation to max 2 sentences.
- **fix: user count accuracy** — `_read_live_stats()` now excludes test profiles (`WHERE user_id NOT LIKE 'test:%'`). Self-context prompt adds explicit anti-hallucination directive (`DATO EXACTO: NO inventes ni redondees estos números`).

### 2026-05-28
- **feat: public dashboard endpoints** — New `/v1/dashboard/*` routes (no auth required) serving real data from both `vectrax.db` (gravitational) and `user_memory.db` (Telegram): stars, constellations, interactions, users, operator, proposals, audit (118K+ entries).
- **fix: self-screenshot analysis** — Vectrax self-recognition now bypasses Visual Humanizer and Anti-Repetition Filter. Technical prompt with 5-step structured analysis. `temperature=0.3`, `max_tokens=1000`, `detail=high` for precise OCR. Real-time system metrics injected for comparison.

## 📄 License

MIT License - See LICENSE file for details

## 🙏 Acknowledgments

Built with:
- [Ollama](https://ollama.ai/) - Local LLM runtime
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [PyYAML](https://pyyaml.org/) - Configuration management

## 📞 Support

- Documentation: See `docs/` directory
- Issues: Create an issue on the repository
- Discussions: Use GitHub Discussions

---

**Made with 🤖 by the Vectrax community**

*Autonomous. Local. Unstoppable.*
