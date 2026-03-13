# Vectrax Platform — Installation Guide

## Prerequisites

- Python 3.9+
- pip
- (Optional) Docker & Docker Compose
- (Optional) Ollama for local LLM

## Quick Install (Local)

```bash
# Clone the repo
git clone <repo-url> ~/Vectrax
cd ~/Vectrax

# Run the installer
bash install.sh

# Activate the environment
source .venv/bin/activate
```

## Manual Install

```bash
cd ~/Vectrax
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install pydantic httpx pytest
```

## Running the Platform

### Core Central Service

```bash
make run-core
# or directly:
PYTHONPATH=. uvicorn services.core.app:app --host 0.0.0.0 --port 8900 --reload
```

The API will be available at `http://localhost:8900/v1/`.

### Local Agent

```bash
make run-agent
# or via CLI:
vx agent start
```

### Docker

```bash
docker-compose up
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VX_ENV` | `dev` | Environment (dev/local/production) |
| `VX_API_TOKEN` | `vx-dev-token-local` | API auth token |
| `VX_CORE_HOST` | `0.0.0.0` | Core service bind host |
| `VX_CORE_PORT` | `8900` | Core service port |
| `VX_CORE_URL` | `http://localhost:8900` | Agent's Core URL |
| `VX_AGENT_ID` | `local-agent-001` | Agent identifier |
| `VX_AGENT_MODE` | `online` | Agent mode (online/offline) |

### Config Files

- `config/config.yaml` — System configuration
- `config/autonomy.json` — Autonomy policy, roles, gates

## Running Tests

```bash
make test
# or:
PYTHONPATH=. pytest tests/ -v
```

## CLI Commands

```bash
vx status                    # System status
vx propose "description"     # Generate proposal (local)
vx propose --remote "desc"   # Generate proposal (via Core API)
vx agent start               # Start agent daemon
vx agent status              # Agent status
vx models                    # List available models
```
