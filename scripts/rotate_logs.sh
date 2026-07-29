#!/usr/bin/env bash
# VECTRAX — Rotación de logs con retención.
#
# - Snapshot de los logs (repo logs/ + sueltos + servicio ~/.vectrax) a
#   ~/vectrax_backups/logs/ como tar.gz con timestamp.
# - Retención: conserva los últimos VX_LOG_RETENTION archivos (por defecto 8).
# - Truncado seguro EN SITIO (`: > fichero`) SOLO de los logs activos
#   (mtime < 7 días): preserva el inode, así los escritores en modo append
#   (launchd, logging de Python) siguen escribiendo sin romperse. Los logs
#   ya quedaron archivados antes de truncar.
#
# Ajustables por entorno:
#   VX_LOG_RETENTION   nº de archivos a conservar (por defecto 8)
#   VX_LOG_TRUNCATE    1 = truncar tras archivar (por defecto), 0 = no tocar
set -uo pipefail

REPO="/Users/mariobravo/Vectrax"
HOME_VX="$HOME/.vectrax"
DEST="$HOME/vectrax_backups/logs"
RETENTION="${VX_LOG_RETENTION:-8}"
TRUNCATE="${VX_LOG_TRUNCATE:-1}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$HOME/vectrax_backups/rotate_logs.log"
STAGE="$(mktemp -d)"

mkdir -p "$DEST"
log(){ printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

log "== inicio rotación logs $STAMP =="

# --- snapshot ---
mkdir -p "$STAGE/repo" "$STAGE/service"
[ -d "$REPO/logs" ] && { mkdir -p "$STAGE/repo/logs"; cp -R "$REPO/logs/." "$STAGE/repo/logs/" 2>>"$LOG"; }
for f in launch.log daemon.log memoria_total_vectrax.log; do
  [ -f "$REPO/$f" ] && cp "$REPO/$f" "$STAGE/repo/" 2>>"$LOG"
done
[ -f "$REPO/memoria/vibracion_musical.log" ] && { mkdir -p "$STAGE/repo/memoria"; cp "$REPO/memoria/vibracion_musical.log" "$STAGE/repo/memoria/" 2>>"$LOG"; }
[ -d "$HOME_VX" ] && find "$HOME_VX" -maxdepth 1 -type f \( -name "*.log" -o -name "*.err" \) -exec cp {} "$STAGE/service/" \; 2>>"$LOG"

ARCHIVE="$DEST/vectrax_logs_$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" . 2>>"$LOG"
rm -rf "$STAGE"
log "archivo: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# --- truncado seguro en sitio de logs ACTIVOS (mtime < 7d) ---
if [ "$TRUNCATE" = "1" ]; then
  {
    find "$REPO/logs" -maxdepth 1 -type f -name "*.log" -mtime -7 2>/dev/null
    for f in launch.log daemon.log memoria_total_vectrax.log memoria/vibracion_musical.log; do
      [ -f "$REPO/$f" ] && [ -n "$(find "$REPO/$f" -mtime -7 2>/dev/null)" ] && printf '%s\n' "$REPO/$f"
    done
    find "$HOME_VX" -maxdepth 1 -type f \( -name "*.log" -o -name "*.err" \) -mtime -7 2>/dev/null
  } | sort -u | while IFS= read -r f; do
    : > "$f" && log "truncado: $f"
  done
fi

# --- retención ---
ls -1t "$DEST"/vectrax_logs_*.tar.gz 2>/dev/null | awk -v n="$RETENTION" 'NR>n' | while IFS= read -r old; do
  rm -f "$old"; log "retención: eliminé $(basename "$old")"
done

log "== fin rotación logs =="
