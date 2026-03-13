#!/usr/bin/env bash
# Vectrax Platform — Local Install Script
# ==========================================
# Creates venv, installs deps, sets up `vx` CLI.
# Usage: bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
RUNTIME_DIR="${HOME}/.vectrax"

echo "🚀 Vectrax Platform Installer"
echo "=============================="
echo ""

# 1. Create venv
if [ ! -d "${VENV_DIR}" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "${VENV_DIR}"
else
    echo "📦 Virtual environment already exists."
fi

# 2. Activate and install
echo "📥 Installing dependencies..."
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q
pip install -e "${SCRIPT_DIR}" -q
pip install pydantic httpx pytest -q

# 3. Create runtime directory
echo "📂 Setting up runtime directory..."
mkdir -p "${RUNTIME_DIR}"
mkdir -p "${SCRIPT_DIR}/vault"
mkdir -p "${SCRIPT_DIR}/logs"
mkdir -p "${SCRIPT_DIR}/reports"

# 4. Verify
echo ""
echo "✅ Installation complete!"
echo ""
echo "USAGE:"
echo "  source ${VENV_DIR}/bin/activate"
echo ""
echo "  vx status                  # Check system status"
echo "  vx propose \"description\"   # Create a proposal"
echo "  vx agent start             # Start the local agent"
echo "  vx agent status            # Check agent status"
echo ""
echo "  make run-core              # Start Core Central Service"
echo "  make run-agent             # Start Agent daemon"
echo "  make test                  # Run tests"
echo ""
