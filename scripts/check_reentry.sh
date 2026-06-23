#!/usr/bin/env bash
# check_reentry.sh — Monitor de presencia: máquina de 3 nudges por usuario.
#
# Uso:
#   bash scripts/check_reentry.sh          # ejecutar una vez
#   watch -n 3600 bash scripts/check_reentry.sh  # cada hora
#
# Nudge #1: 12-20h | Nudge #2: 3-5d | Nudge #3: 21-30d → DORMANT

SSH="ssh -i $HOME/.ssh/vectrax_server root@140.82.28.181"
LOG="/root/.vectrax/worker.log"

echo "=== Presence Nudge Monitor (3-nudge state machine) ==="
echo "$(date)"
echo ""

N1=$($SSH   "docker exec vectrax-core grep -c 'presence_nudge #1 sent' $LOG 2>/dev/null" 2>/dev/null || echo 0)
N2=$($SSH   "docker exec vectrax-core grep -c 'presence_nudge #2 sent' $LOG 2>/dev/null" 2>/dev/null || echo 0)
N3=$($SSH   "docker exec vectrax-core grep -c 'presence_nudge #3 sent' $LOG 2>/dev/null" 2>/dev/null || echo 0)
BLOCKED=$($SSH "docker exec vectrax-core grep -c 'GUARD.*reason=reentry' $LOG 2>/dev/null" 2>/dev/null || echo 0)
DORMANT=$($SSH "docker exec vectrax-core python3 -c \
    \"import sqlite3,os; db='/opt/vectrax/vault/continuity_reentry.db'; \
    c=sqlite3.connect(db); \
    print(c.execute(\\\"SELECT COUNT(*) FROM reentry_state WHERE status=\'dormant\'\\\").fetchone()[0]); \
    c.close()\" 2>/dev/null" 2>/dev/null || echo '?')

echo "Nudge #1 sent : $N1"
echo "Nudge #2 sent : $N2"
echo "Nudge #3 sent : $N3"
echo "Blocked       : $BLOCKED"
echo "DORMANT users : $DORMANT"
echo ""

if [ "$BLOCKED" -gt 0 ] 2>/dev/null; then
    echo "❌ ALERT: $BLOCKED nudges bloqueados por telegram_guard!"
    $SSH "docker exec vectrax-core grep 'GUARD.*reason=reentry' $LOG 2>/dev/null | tail -3" 2>/dev/null
    exit 1
else
    echo "✅ Sin bloqueos."
fi

echo ""
echo "-- Últimos 5 nudges enviados --"
$SSH "docker exec vectrax-core grep 'presence_nudge #' $LOG 2>/dev/null | tail -5" 2>/dev/null

echo ""
echo "-- Estado DB (nudge_count, status) --"
$SSH "docker exec vectrax-core python3 -c \
    \"import sqlite3; db='/opt/vectrax/vault/continuity_reentry.db'; \
    c=sqlite3.connect(db); \
    [print(r) for r in c.execute('SELECT user_id, nudge_count, status FROM reentry_state ORDER BY nudge_count DESC LIMIT 10').fetchall()]; \
    c.close()\" 2>/dev/null" 2>/dev/null
