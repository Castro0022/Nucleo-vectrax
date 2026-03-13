#!/bin/bash
# Vectrax CLI Activation Script
# Source this file to enable the vx command in your current shell
# Usage: source activate_vx.sh

# Activate virtual environment
source "$(dirname "$0")/.venv/bin/activate"

echo "✅ Vectrax CLI activated!"
echo "   Run 'vx help' to get started"
echo "   Run 'deactivate' to exit"
