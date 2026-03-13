#!/bin/bash
# vectrax_start.sh — Sovereign startup delegation
# Delegates entirely to the single runtime authority: vx activate
# Do NOT start vectrax_unified.py here; use 'vx run' for a persistent loop.

mkdir -p ~/.vectrax

# Ensure we are in the Vectrax directory with the venv active
cd "$(dirname "$0")"
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

exec vx activate
