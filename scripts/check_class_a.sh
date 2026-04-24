#!/usr/bin/env bash
# One-shot health snapshot of Class A in real mode.
# Run anytime to confirm no BROKEN / no rogue executions.
# Usage: bash scripts/check_class_a.sh

SSH_KEY="${HOME}/.ssh/vectrax_server"
SERVER="root@140.82.28.181"

ssh -i "$SSH_KEY" "$SERVER" '
echo "=== Time (UTC) ==="
date -u
echo ""
echo "=== BROKEN events ever (must be 0) ==="
docker exec vectrax-core grep -c "\"state\": \"BROKEN\"" /var/log/vectrax/recovery_ledger.jsonl 2>/dev/null || echo 0
echo ""
echo "=== execution + escalation entries (strategy actually ran?) ==="
docker exec vectrax-core sh -c "grep -cE \"\\\"kind\\\": \\\"(execution|escalation)\\\"\" /var/log/vectrax/recovery_ledger.jsonl 2>/dev/null || echo 0"
echo ""
echo "=== Last gateway_silent state ==="
docker exec vectrax-core sh -c "grep gateway_silent /var/log/vectrax/recovery_ledger.jsonl 2>/dev/null | tail -1 | python3 -c \"import sys,json; d=json.loads(sys.stdin.read()); p=d[chr(39)+chr(112)+chr(97)+chr(121)+chr(108)+chr(111)+chr(97)+chr(100)+chr(39)]; print(p[chr(39)+chr(115)+chr(116)+chr(97)+chr(116)+chr(101)+chr(39)], p[chr(39)+chr(101)+chr(118)+chr(105)+chr(100)+chr(101)+chr(110)+chr(99)+chr(101)+chr(39)])\" 2>/dev/null || tail -1 /var/log/vectrax/recovery_ledger.jsonl"
echo ""
echo "=== Container status ==="
docker ps --filter name=vectrax-core --format "{{.Names}} {{.Status}}"
'
