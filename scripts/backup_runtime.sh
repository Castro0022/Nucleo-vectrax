#!/usr/bin/env bash
# ============================================================
# Vectrax Runtime Backup
# ============================================================
# Toma un snapshot del volume persistente vectrax-runtime y
# lo guarda como tarball local con timestamp.
#
# Uso (local):
#   bash scripts/backup_runtime.sh
#
# Uso (en producción vía SSH):
#   ssh root@vectrax bash /opt/vectrax/scripts/backup_runtime.sh
#
# Salida:
#   ~/vectrax_backups/runtime-YYYYMMDD-HHMMSS.tar.gz
#
# Contiene:
#   continuity.db, anti_repetition.db, vectrax.db,
#   sovereignty.jsonl, gateway.log, worker.log,
#   supervisor.log, heartbeats, vctx_identity seed.
# ============================================================

# NOTA: NO se usa 'set -e' / 'set -u' a propósito; queremos que el
# script siga aunque un archivo individual falle.

TS=$(date +%Y%m%d-%H%M%S)
DEST="${VECTRAX_BACKUP_DIR:-$HOME/vectrax_backups}"
mkdir -p "$DEST"

# Detectar si corremos dentro o fuera del container.
if [ -d /root/.vectrax ]; then
  RUNTIME_DIR=/root/.vectrax
elif [ -d "$HOME/.vectrax" ]; then
  RUNTIME_DIR="$HOME/.vectrax"
else
  echo "No se encontró /root/.vectrax ni ~/.vectrax. Abortando."
  exit 1
fi

OUT="$DEST/runtime-$TS.tar.gz"
echo "  Backup desde : $RUNTIME_DIR"
echo "  Destino      : $OUT"

# WAL/SHM podrían estar abiertos; tar incluye lo que pueda.
tar czf "$OUT" -C "$(dirname "$RUNTIME_DIR")" "$(basename "$RUNTIME_DIR")" 2>/dev/null

if [ -f "$OUT" ]; then
  size=$(du -h "$OUT" | cut -f1)
  echo "  Backup OK    : $size"
  ls -lah "$DEST" | tail -10
else
  echo "  Backup FAILED"
  exit 2
fi

# Retener solo los últimos 30 backups; borrar el resto.
cd "$DEST" || exit 0
ls -1t runtime-*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm -- 2>/dev/null
echo "  Retención    : 30 backups recientes"
