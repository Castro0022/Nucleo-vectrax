#!/usr/bin/env bash
# VECTRAX — Backup consistente de la BASE DE DATOS (SQLite + ledgers).
#
# - Copia ONLINE de cada SQLite con `.backup` (seguro con WAL y escrituras
#   concurrentes; nunca deja una copia a medias como haría un `cp` plano).
# - Snapshot de los ledgers JSONL/JSON (vault/ + data/).
# - Empaqueta con timestamp en ~/vectrax_backups/db/, calcula sha256,
#   aplica retención y espeja a iCloud (durabilidad FUERA de este disco).
# - No destructivo: jamás modifica las bases de datos originales.
#
# Ajustables por entorno:
#   VX_DB_RETENTION   nº de archivos a conservar (por defecto 14)
set -uo pipefail

REPO="/Users/mariobravo/Vectrax"
HOME_VX="$HOME/.vectrax"
DEST="$HOME/vectrax_backups/db"
ICLOUD_BASE="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
ICLOUD="$ICLOUD_BASE/vectrax_backups/db"
RETENTION="${VX_DB_RETENTION:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$HOME/vectrax_backups/backup_db.log"
STAGE="$(mktemp -d)"
SQLITE="/usr/bin/sqlite3"

mkdir -p "$DEST"
log(){ printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

ok=0; fail=0

backup_sqlite(){
  local src="$1" dst="$2"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$dst")"
  if "$SQLITE" -cmd ".timeout 8000" "$src" ".backup '$dst'" 2>>"$LOG"; then
    ok=$((ok+1))
  else
    # Fallback: copia del fichero + sidecars WAL/SHM
    if cp "$src" "$dst" 2>>"$LOG"; then
      [ -f "$src-wal" ] && cp "$src-wal" "$dst-wal" 2>>"$LOG"
      [ -f "$src-shm" ] && cp "$src-shm" "$dst-shm" 2>>"$LOG"
      log "WARN .backup falló; copié fichero+WAL: $src"
      ok=$((ok+1))
    else
      log "ERROR no pude respaldar: $src"
      fail=$((fail+1))
    fi
  fi
}

log "== inicio backup DB $STAMP =="

# 1) SQLite del repo (sin obsoletos), preservando ruta relativa
while IFS= read -r db; do
  rel="${db#"$REPO"/}"
  backup_sqlite "$db" "$STAGE/repo/$rel"
done < <(find "$REPO" -type f -name "*.db" \
           -not -path "*/.venv/*" -not -path "*/.git/*" \
           -not -path "*/archive/obsoletos_*/*")

# 2) SQLite en ~/.vectrax
if [ -d "$HOME_VX" ]; then
  while IFS= read -r db; do
    rel="${db#"$HOME_VX"/}"
    backup_sqlite "$db" "$STAGE/home_vectrax/$rel"
  done < <(find "$HOME_VX" -maxdepth 1 -type f -name "*.db")
fi

# 3) Ledgers JSONL/JSON (vault/ + data/)
for base in vault data; do
  [ -d "$REPO/$base" ] || continue
  while IFS= read -r f; do
    rel="${f#"$REPO"/}"
    mkdir -p "$STAGE/repo/$(dirname "$rel")"
    if cp "$f" "$STAGE/repo/$rel" 2>>"$LOG"; then ok=$((ok+1)); else log "ERROR ledger: $f"; fail=$((fail+1)); fi
  done < <(find "$REPO/$base" -type f \( -name "*.jsonl" -o -name "*.json" \))
done

# 4) Manifiesto
{
  echo "VECTRAX DB backup"
  echo "stamp: $STAMP"
  echo "host: $(hostname)"
  echo "sqlite: $("$SQLITE" --version 2>/dev/null)"
  echo "--- ficheros ---"
  ( cd "$STAGE" && find . -type f -exec du -h {} + | sort -k2 )
} > "$STAGE/MANIFEST.txt" 2>/dev/null

# 5) Empaquetar + checksum
ARCHIVE="$DEST/vectrax_db_$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" . 2>>"$LOG"
rm -rf "$STAGE"
( cd "$DEST" && shasum -a 256 "$(basename "$ARCHIVE")" > "$ARCHIVE.sha256" )
SIZE="$(du -h "$ARCHIVE" | cut -f1)"
log "archivo local: $ARCHIVE ($SIZE) ok=$ok fail=$fail"

# 6) Espejo iCloud (fuera de este disco)
if [ -d "$ICLOUD_BASE" ]; then
  mkdir -p "$ICLOUD"
  if cp "$ARCHIVE" "$ARCHIVE.sha256" "$ICLOUD/" 2>>"$LOG"; then
    log "espejo iCloud: $ICLOUD/$(basename "$ARCHIVE")"
  else
    log "WARN no pude espejar a iCloud"
  fi
else
  log "WARN iCloud no disponible; solo copia local"
fi

# 7) Retención (local + iCloud): conservar los últimos $RETENTION
prune(){
  local dir="$1"
  ls -1t "$dir"/vectrax_db_*.tar.gz 2>/dev/null | awk -v n="$RETENTION" 'NR>n' | while IFS= read -r old; do
    rm -f "$old" "$old.sha256"; log "retención: eliminé $(basename "$old")"
  done
}
prune "$DEST"
[ -d "$ICLOUD" ] && prune "$ICLOUD"

log "== fin backup DB (ok=$ok fail=$fail) =="
[ "$fail" -eq 0 ]
