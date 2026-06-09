#!/usr/bin/env bash
# check_restarts.sh — Alerta si el worker se reinicia inesperadamente.
#
# Uso:
#   bash scripts/check_restarts.sh        # ejecutar una vez
#   Cron: 0 * * * * bash ~/vectrax/scripts/check_restarts.sh >> ~/.vectrax/restart_monitor.log 2>&1
#
# Mantiene un baseline en ~/.vectrax/restart_baseline.
# Si el conteo sube más de 1 (deploy), alerta.

SSH="ssh -i $HOME/.ssh/vectrax_server root@140.82.28.181"
BASELINE_FILE="$HOME/.vectrax/restart_baseline"

echo "=== Restart Monitor ==="
echo "$(date)"

# Get current count
CURRENT=$($SSH "docker exec vectrax-core grep -c 'Worker started' /root/.vectrax/worker.log 2>/dev/null" 2>/dev/null || echo 0)

# Load baseline
if [ -f "$BASELINE_FILE" ]; then
    BASELINE=$(cat "$BASELINE_FILE")
else
    BASELINE=$CURRENT
    echo "$CURRENT" > "$BASELINE_FILE"
    echo "Baseline set: $CURRENT"
    echo "✅ First run — no comparison."
    exit 0
fi

DIFF=$((CURRENT - BASELINE))

echo "Baseline: $BASELINE"
echo "Current:  $CURRENT"
echo "New restarts: $DIFF"

if [ "$DIFF" -gt 2 ]; then
    echo "❌ ALERT: $DIFF new restarts since baseline!"
    echo "Last 3 restarts:"
    $SSH "docker exec vectrax-core grep 'Worker started' /root/.vectrax/worker.log 2>/dev/null | tail -3" 2>/dev/null
    echo ""
    echo "Timeouts:"
    $SSH "docker exec vectrax-core grep -c 'GW_TIMEOUT\|CONV_TIMEOUT' /root/.vectrax/worker.log 2>/dev/null" 2>/dev/null
    exit 1
elif [ "$DIFF" -le 1 ]; then
    echo "✅ Stable. At most 1 restart (deploy)."
    # Update baseline after deploy
    echo "$CURRENT" > "$BASELINE_FILE"
else
    echo "⚠️ 2 restarts — monitor closely."
fi
