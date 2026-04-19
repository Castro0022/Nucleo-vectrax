#!/usr/bin/env bash
# ============================================================
# vectrax_boot.sh — Único entrypoint local de Vectrax
# ============================================================
# Usado por:
#   - com.vectrax.supervisor.plist  (launchd → autoarranque Mac)
#   - CLI manual:  bash scripts/vectrax_boot.sh
#
# NO edites otros launchers. Este es el único.
# Si este script falla, Vectrax no arranca. Simple.
# ============================================================

set -euo pipefail

VECTRAX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$VECTRAX_DIR/.venv"
SUPERVISOR="$VECTRAX_DIR/vectrax_supervisor.py"
LOG_DIR="$HOME/.vectrax/logs"

# ── Validaciones previas ──────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "[vectrax_boot] FATAL: venv no encontrado en $VENV" >&2
    echo "[vectrax_boot] Ejecuta: make dev" >&2
    exit 1
fi

PYTHON_BIN="$VENV/bin/python"
REAL_PYTHON=$(python3 -c "import sys; print(sys.executable)" 2>/dev/null || true)

# Verificar que el Python del venv no apunta a otro proyecto
PYTHON_LOCATION=$(readlink -f "$VENV/bin/python3" 2>/dev/null || echo "$VENV/bin/python3")
if echo "$PYTHON_LOCATION" | grep -qv "vectrax\|Vectrax"; then
    # Python en venv apunta a ruta externa — usar sistema como base
    echo "[vectrax_boot] WARN: .venv/python3 apunta a ruta externa: $PYTHON_LOCATION" >&2
    echo "[vectrax_boot] Usando python3 del sistema con PYTHONPATH." >&2
    PYTHON_BIN="$(which python3)"
fi

if [ ! -f "$SUPERVISOR" ]; then
    echo "[vectrax_boot] FATAL: vectrax_supervisor.py no encontrado en $SUPERVISOR" >&2
    exit 1
fi

# ── Preparar entorno ──────────────────────────────────────
mkdir -p "$LOG_DIR"
export PYTHONPATH="$VECTRAX_DIR"

# Activar venv si existe y es válido
if [ -f "$VENV/bin/activate" ]; then
    # shellcheck disable=SC1090
    source "$VENV/bin/activate" 2>/dev/null || true
fi

# ── Arranque ──────────────────────────────────────────────
echo "[vectrax_boot] Iniciando Vectrax Supervisor desde: $VECTRAX_DIR"
echo "[vectrax_boot] Python: $(which python3)"
echo "[vectrax_boot] Timestamp: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

exec python3 "$SUPERVISOR"
