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
- **LawSignal** - Connects the 7 Fundamental Laws (Kybalion-inspired) as active score weights: violations reduce sovereignty/convergence or raise noise before PresenciaObserver decides. The principles don’t respond. They weigh.

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
        ├─ check_reentry()       → sends ONE contextual message
        │                           sets reentry_sent=1
        │
        └─ [user returns]        → record_activity() resets everything
                                    next cycle starts fresh
```

Persistence: `vault/continuity_reentry.db` (SQLite).

## 📊 Router Performance

```bash
# Default report (last 200 interactions)
python3 scripts/router_report.py

# Compare pre/post deploy
python3 scripts/router_report.py --since 2026-05-23T00:55 --compare

# JSON output for automation
python3 scripts/router_report.py --json

# On production server
ssh root@140.82.28.181 'cd /opt/vectrax && docker compose exec -T vectrax \
  python3 /app/scripts/router_report.py --last 200'
```

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
