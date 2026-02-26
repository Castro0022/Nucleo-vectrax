#!/bin/bash
set -e

mkdir -p ~/.vectrax

# Arranque persistente (no muere si cierras Warp)
nohup python3 ~/Vectrax/vectrax_unified.py >> ~/.vectrax/vectrax.boot.log 2>&1 &

# Soltar el job del shell (extra seguro)
disown || true

echo "⟡ [Vectrax] Arranque enviado. (Si ya estaba activo, no se duplicará.)"
