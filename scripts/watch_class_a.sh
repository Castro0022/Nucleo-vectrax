#!/usr/bin/env bash
# ============================================
# watch_class_a.sh — live ledger watch for Class A in real mode
# ============================================
# Streams the remote recovery_ledger and highlights:
#   - BROKEN events (detector escalated)
#   - execution entries (strategy RAN for real)
#   - escalation entries (throttle / rollback / human needed)
# Non-BROKEN events (OK / DEGRADED) are NOT printed — zero noise.
#
# Exits only on Ctrl-C.
#
# Usage:
#   bash scripts/watch_class_a.sh
# ============================================

SSH_KEY="${HOME}/.ssh/vectrax_server"
SERVER="root@140.82.28.181"
LEDGER_PATH="/var/log/vectrax/recovery_ledger.jsonl"

echo "=========================================="
echo "  CLASS A WATCH — live ledger tail"
echo "  Only BROKEN / execution / escalation surface."
echo "  Ctrl-C to exit."
echo "=========================================="
echo ""

# tail -F (capital F) survives file rotation.
# The grep filter keeps only actionable lines; awk adds a timestamp and
# colors the line so BROKEN stands out in the terminal.
ssh -i "$SSH_KEY" -o ServerAliveInterval=30 "$SERVER" \
  "docker exec vectrax-core tail -F -n 0 $LEDGER_PATH" \
  | grep --line-buffered -E '"state": "BROKEN"|"kind": "execution"|"kind": "escalation"' \
  | while IFS= read -r line; do
      ts=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
      if echo "$line" | grep -q '"state": "BROKEN"'; then
        printf "\033[1;31m[%s][BROKEN]\033[0m %s\n" "$ts" "$line"
      elif echo "$line" | grep -q '"kind": "escalation"'; then
        printf "\033[1;35m[%s][ESCAL ]\033[0m %s\n" "$ts" "$line"
      else
        printf "\033[1;33m[%s][EXEC  ]\033[0m %s\n" "$ts" "$line"
      fi
    done
