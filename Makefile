# Vectrax Platform — Makefile
# ============================
# Usage: make <target>

SHELL := /bin/bash
PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
VX := $(VENV)/bin/vx

.PHONY: help dev test test-integration lint run-core run-agent install clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

dev:  ## Install dev dependencies
	$(PYTHON) -m venv $(VENV) || true
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]" 2>/dev/null || $(PIP) install -e .
	$(PIP) install pytest httpx pydantic
	@echo "✅ Dev environment ready. Activate with: source $(VENV)/bin/activate"

test:  ## Run all tests
	PYTHONPATH=. $(PYTEST) tests/ -v --tb=short

test-integration:  ## Run integration tests only (fast, no external deps)
	PYTHONPATH=. $(PYTEST) tests/integration/ -v --tb=short

lint:  ## Run linting (basic syntax check)
	$(PYTHON) -m py_compile services/core/app.py
	$(PYTHON) -m py_compile agent/daemon.py
	$(PYTHON) -m py_compile connectors/registry.py
	$(PYTHON) -m py_compile core/audit_ledger.py
	@echo "✅ Lint passed (syntax OK)"

run-core:  ## Start Core Central Service on port 8900
	PYTHONPATH=. $(UVICORN) services.core.app:app --host 0.0.0.0 --port 8900 --reload

run-service:  ## Start Vectrax as a full service (API + runtime)
	PYTHONPATH=. $(PYTHON) -m services.runtime --no-reload

run-agent:  ## Start Local Agent daemon
	PYTHONPATH=. $(PYTHON) -c "import asyncio; from agent.daemon import run_agent; asyncio.run(run_agent())"

install:  ## Full local install (venv + deps + vx CLI)
	bash install.sh

clean:  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache build dist
	@echo "✅ Cleaned"
