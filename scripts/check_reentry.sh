#!/usr/bin/env bash
# check_reentry.sh — Verifica que los mensajes de reentry llegan a los usuarios.
#
# Uso:
#   bash scripts/check_reentry.sh          # ejecutar una vez
#   watch -n 3600 bash scripts/check_reentry.sh  # cada hora
#
# Lo que busca:
#   ✅ "Reentry sent" SIN "GUARD blocked reentry" antes → OK
#   ❌ "GUARD blocked (reason=reentry)" → BUG, el guard sigue bloqueando

SSH="ssh -i $HOME/.ssh/vectrax_server root@140.82.28.181"
LOG="/root/.vectrax/worker.log"

echo "=== Reentry Monitor ==="
echo "$(date)"
echo ""

# Last 24h of reentry activity
SENT=$($SSH "docker exec vectrax-core grep -c 'Reentry sent' $LOG 2>/dev/null" 2>/dev/null || echo 0)
BLOCKED=$($SSH "docker exec vectrax-core grep -c 'GUARD.*reason=reentry' $LOG 2>/dev/null" 2>/dev/null || echo 0)
GENERATED=$($SSH "docker exec vectrax-core grep -c 'Reentry.*generated' $LOG 2>/dev/null" 2>/dev/null || echo 0)

echo "Generated: $GENERATED"
echo "Sent:      $SENT"
echo "Blocked:   $BLOCKED"
echo ""

if [ "$BLOCKED" -gt 0 ] 2>/dev/null; then
    echo "❌ ALERT: $BLOCKED reentry messages blocked by telegram_guard!"
    echo "Last blocked:"
    $SSH "docker exec vectrax-core grep 'GUARD.*reason=reentry' $LOG 2>/dev/null | tail -3" 2>/dev/null
    exit 1
else
    echo "✅ No blocked reentry messages."
fi

echo ""
echo "Last 5 reentry events:"
$SSH "docker exec vectrax-core grep -E 'Reentry.*(sent|generated)' $LOG 2>/dev/null | tail -5" 2>/dev/null
