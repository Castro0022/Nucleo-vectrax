# Vectrax Platform — Architecture

## Overview

Vectrax is a **local-first autonomous AI infrastructure** with a platform architecture:

```
┌──────────────────────────────────────────┐
│              Core Central Service         │
│  (FastAPI, /v1/ API, port 8900)          │
│                                          │
│  /v1/health         — health check       │
│  /v1/auth/login     — authentication     │
│  /v1/connectors     — connector listing  │
│  /v1/events         — event ingestion    │
│  /v1/actions/propose — remote proposals  │
└────────────────┬─────────────────────────┘
                 │ REST (Bearer token)
┌────────────────▼─────────────────────────┐
│             Local Agent                   │
│  (CLI daemon, online/offline)            │
│                                          │
│  - Registers with Core                   │
│  - Sends events (heartbeat, status)      │
│  - Fetches proposals from Core           │
│  - Routes proposals through              │
│    ProposalEngine → RiskEngine → Governor│
│  - Offline queue for disconnected mode   │
└──────────────────────────────────────────┘
```

## Components

### Core Central Service (`services/core/`)
- FastAPI application with versioned `/v1/` routes
- Bearer token authentication (env var `VX_API_TOKEN`)
- Event ingestion from agents
- Remote proposal generation
- Connector registry integration
- Capabilities registry for future rate limits

### Local Agent (`agent/`)
- HTTP client communicating with Core
- Online mode: pushes events, fetches proposals
- Offline mode: queues events locally, syncs when online
- CLI: `vx agent start`, `vx agent status`

### Connectors (`connectors/`)
- Plugin system with `ConnectorSpec` + `ConnectorAdapter` ABC
- Built-in stubs: Google Workspace, iCloud, Generic Webhook
- Each connector: auth handshake, list resources, healthcheck, test

### Core Engine (`core/`)
- **Governor** — Policy engine with modes: observe, act, cautious, recover
- **RiskEngine** — 6-signal probabilistic risk assessment
- **AutonomyPolicy** — Three-zone classification (SACRED_CORE, SEMI_SAFE, FLEXIBLE)
- **ProposalEngine** — LLM-powered code change proposals
- **PolicyGates** — Named gates for change categories
- **AuditLedger** — Append-only SQLite audit trail
- **Roles** — owner, operator, viewer with permission maps
- **Criterion Engine** (`core/learn/criterion.py`) — cross-domain learned opinion from persisted evidence (grounded, never fabricates); registered engine #48 in `core/orchestration/engine_registry.py`

### Observability (`observability/`)
- Stable JSON event schema (VectraxEvent)
- Daily platform health reports
- Rotating log files

## Security Model

1. **Deny-by-default**: All changes require human confirmation
2. **Bearer token auth**: API protected by `VX_API_TOKEN`
3. **Three-zone policy**: SACRED_CORE (never auto), SEMI_SAFE (confirm), FLEXIBLE (switchable)
4. **Hard limits**: .env, vault/, .git/, keys/, secrets/ — NEVER auto-apply
5. **Audit ledger**: Every action recorded with timestamp, actor, diff hash
6. **Roles**: owner (full), operator (limited), viewer (read-only)
7. **Policy gates**: Sacred core gate + docs/tests/logs gate (default OFF)

## Data Flow

```
User → vx propose "change"
          │
          ├── (local mode) → ProposalEngine → RiskEngine → Governor → Human Review
          │
          └── (--remote)   → Core API → ProposalEngine → RiskEngine → Governor → Human Review
```

## File Layout

```
Vectrax/
├── agent/              # Local Agent
├── cli/                # CLI (vx command)
├── config/             # Configuration files
├── connectors/         # Connector framework + stubs
├── core/               # Core engine modules
├── docs/               # Documentation
├── logs/               # Log files
├── observability/      # Platform telemetry
├── reports/            # Generated reports
├── services/core/      # Core Central Service (FastAPI)
├── tests/              # Test suite
├── vault/              # Audit ledger, secrets
├── Makefile            # Build commands
├── docker-compose.yml  # Docker setup
├── install.sh          # Local installer
└── setup.py            # Python package
```
