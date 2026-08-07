#!/usr/bin/env bash
# scripts/monitor_worker_log.sh — Alerta de monitoreo del log del worker (read-only).
#
# Escanea INCREMENTALMENTE ~/.vectrax/worker.log (cursor por offset de bytes, con
# manejo de rotación/truncado) buscando señales de alerta:
#   [ERROR] · [CRITICAL] · Traceback · TIMEOUT (incl. CYBER_LEARN_TIMEOUT,
#   FREIGHT/REAL_ESTATE_LEARN_TIMEOUT, CONV/GATEWAY) · worker_unresponsive ·
#   heartbeat_stale
# Registra una línea OK/ALERT en ~/.vectrax/worker_alerts.log y, ante alerta,
# emite una notificación de macOS (salvo VX_WORKER_MONITOR_NOTIFY=0). Además
# reporta como INFO cuando el ciclo cyber se ejecuta ("Cyber learn:").
#
# NO modifica el sistema: solo lee worker.log y escribe su propio alerts log.
#
# Uso:
#   scripts/monitor_worker_log.sh                # una pasada incremental
#   scripts/monitor_worker_log.sh --loop N M     # N pasadas cada M segundos
#   scripts/monitor_worker_log.sh --reset        # fija el cursor a EOF (ignora histórico)
#
# Creador: Mario Bravo Castro
set -uo pipefail

VDIR="$HOME/.vectrax"
WLOG="$VDIR/worker.log"
ALERTLOG="$VDIR/worker_alerts.log"
OFFSET_FILE="$VDIR/worker_monitor.offset"
NOTIFY="${VX_WORKER_MONITOR_NOTIFY:-1}"

ALERT_RE='\[(ERROR|CRITICAL)\]|Traceback|TIMEOUT|worker_unresponsive|heartbeat_stale'
CYBER_OK_RE='Cyber learn:'

mkdir -p "$VDIR"

snapshot() {
  local ts size prev newbytes alerts cyber sample info
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if [ ! -f "$WLOG" ]; then
    echo "$ts OK | worker.log ausente (aún no hay actividad)" >> "$ALERTLOG"
    return 0
  fi

  size="$(wc -c < "$WLOG" | tr -d ' ')"
  prev="$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)"
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  # Rotación / truncado: si el archivo encogió, reinicia el cursor a 0.
  [ "$size" -lt "$prev" ] && prev=0

  if [ "$size" -eq "$prev" ]; then
    echo "$ts OK | sin líneas nuevas" >> "$ALERTLOG"
    return 0
  fi

  # Solo el contenido NUEVO desde el offset previo (evita re-alertar histórico).
  newbytes="$(tail -c +"$((prev + 1))" "$WLOG" 2>/dev/null)"
  echo "$size" > "$OFFSET_FILE"

  alerts="$(printf '%s\n' "$newbytes" | grep -Ec "$ALERT_RE" 2>/dev/null)"; alerts="${alerts//[^0-9]/}"; alerts="${alerts:-0}"
  cyber="$(printf '%s\n' "$newbytes" | grep -Ec "$CYBER_OK_RE" 2>/dev/null)"; cyber="${cyber//[^0-9]/}"; cyber="${cyber:-0}"

  if [ "$alerts" -gt 0 ]; then
    sample="$(printf '%s\n' "$newbytes" | grep -E "$ALERT_RE" 2>/dev/null | tail -n 3 | tr '\n' '|' )"
    echo "$ts ALERT | worker.log: $alerts lineas de alerta | cyber_cycles=$cyber | $sample" >> "$ALERTLOG"
    if [ "$NOTIFY" != "0" ] && command -v osascript >/dev/null 2>&1; then
      osascript -e "display notification \"$alerts alertas en worker.log\" with title \"Vectrax worker\" subtitle \"$ts\"" >/dev/null 2>&1 || true
    fi
    return 1
  fi

  info=""
  [ "$cyber" -gt 0 ] && info=" | INFO cyber_cycle_ran=$cyber"
  echo "$ts OK | 0 alertas${info}" >> "$ALERTLOG"
  return 0
}

case "${1:-}" in
  --reset)
    if [ -f "$WLOG" ]; then wc -c < "$WLOG" | tr -d ' ' > "$OFFSET_FILE"; else echo 0 > "$OFFSET_FILE"; fi
    echo "cursor reiniciado a EOF ($(cat "$OFFSET_FILE") bytes)"
    ;;
  --loop)
    N="${2:-12}"; M="${3:-300}"
    for _ in $(seq 1 "$N"); do snapshot; sleep "$M"; done
    ;;
  *)
    snapshot
    ;;
esac
