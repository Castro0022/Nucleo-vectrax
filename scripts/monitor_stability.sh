#!/usr/bin/env bash
# scripts/monitor_stability.sh — Monitor de estabilidad (read-only).
#
# Toma un snapshot del estado de producción y registra una línea OK/ALERT en
# ~/.vectrax/stability_monitor.log. NO modifica el sistema: sólo consulta la API
# local (/v1/health, /v1/universe) y cuenta señales del día en worker.log.
#
# Uso:
#   scripts/monitor_stability.sh                 # una sola pasada
#   scripts/monitor_stability.sh --loop N M      # N pasadas cada M segundos (acotado)
#
# Señales de ALERTA: health != 200, worker caído, cola > 10 pendientes,
# ERROR/CRITICAL del día > 0, o cualquier Traceback en worker.log.
# MEM_LEAK del día se registra como informativo (patrón conocido, no alerta).
#
# Creador: Mario Bravo Castro
set -uo pipefail

VDIR="$HOME/.vectrax"
LOG="$VDIR/stability_monitor.log"
REPO="$HOME/Vectrax"
API="http://localhost:8900"

mkdir -p "$VDIR"

snapshot() {
  local ts today health uni worker_alive queue_pend errors memleak tb flags
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  today="$(date -u +%Y-%m-%d)"

  health="$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/health" 2>/dev/null || echo 000)"

  uni="$(curl -s "$API/v1/universe" 2>/dev/null || echo '')"
  worker_alive="$(printf '%s' "$uni" | "$REPO/.venv/bin/python" -c "import sys,json
try:
    d=json.load(sys.stdin); print(d.get('operational',{}).get('worker_alive'))
except Exception:
    print('ERR')" 2>/dev/null || echo ERR)"
  queue_pend="$(printf '%s' "$uni" | "$REPO/.venv/bin/python" -c "import sys,json
try:
    d=json.load(sys.stdin); print(int(d.get('operational',{}).get('queue',{}).get('pending',0)))
except Exception:
    print(-1)" 2>/dev/null || echo -1)"

  # grep -c imprime 0 y además sale !=0 cuando no hay match; normalizamos a dígitos.
  errors="$(grep -cE "^$today .*\[(ERROR|CRITICAL)\]" "$VDIR/worker.log" 2>/dev/null)"; errors="${errors//[^0-9]/}"; errors="${errors:-0}"
  memleak="$(grep -cE "^$today .*MEM_LEAK" "$VDIR/worker.log" 2>/dev/null)"; memleak="${memleak//[^0-9]/}"; memleak="${memleak:-0}"
  tb="$(grep -c 'Traceback' "$VDIR/worker.log" 2>/dev/null)"; tb="${tb//[^0-9]/}"; tb="${tb:-0}"

  flags=""
  [ "$health" != "200" ] && flags="$flags health=$health"
  [ "$worker_alive" != "True" ] && flags="$flags worker_down($worker_alive)"
  [ "$queue_pend" -gt 10 ] 2>/dev/null && flags="$flags queue=$queue_pend"
  [ "$errors" -gt 0 ] 2>/dev/null && flags="$flags errors=$errors"
  [ "$tb" -gt 0 ] 2>/dev/null && flags="$flags tracebacks=$tb"

  if [ -n "$flags" ]; then
    echo "$ts ALERT |$flags | memleak_today=$memleak" >> "$LOG"
  else
    echo "$ts OK | health=$health worker_alive=$worker_alive queue=$queue_pend errors_today=$errors memleak_today=$memleak tb=$tb" >> "$LOG"
  fi
}

if [ "${1:-}" = "--loop" ]; then
  N="${2:-16}"; M="${3:-900}"
  for _ in $(seq 1 "$N"); do
    snapshot
    sleep "$M"
  done
else
  snapshot
fi
