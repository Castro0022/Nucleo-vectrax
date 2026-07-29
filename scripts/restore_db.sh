#!/usr/bin/env bash
# VECTRAX — Restaurar un backup de la BD a un directorio destino.
#
# Uso:
#   scripts/restore_db.sh [STAMP|latest] [DEST_DIR]
#     STAMP     p. ej. 20260729_132005  ('latest' por defecto)
#     DEST_DIR  destino (si se omite, se crea un mktemp -d)
#
# Qué hace (y qué NO):
# - Verifica el `.sha256` del archivo ANTES de extraer.
# - Extrae el tar.gz en DEST_DIR y corre `PRAGMA integrity_check` en cada `.db`.
# - Imprime un resumen; la ÚLTIMA línea de stdout es la ruta de DEST_DIR.
# - NO toca las bases vivas: solo escribe dentro de DEST_DIR. Para aplicar una
#   restauración, copia manualmente el `.db` que necesites (con el sistema parado).
set -uo pipefail

DEST_BASE="${VX_DB_BACKUP_DIR:-$HOME/vectrax_backups/db}"
SEL="${1:-latest}"
OUT="${2:-}"

if [ "$SEL" = "latest" ]; then
  ARCHIVE="$(ls -1t "$DEST_BASE"/vectrax_db_*.tar.gz 2>/dev/null | head -1)"
else
  ARCHIVE="$DEST_BASE/vectrax_db_$SEL.tar.gz"
fi
if [ -z "${ARCHIVE:-}" ] || [ ! -f "$ARCHIVE" ]; then
  echo "ERROR: no encuentro backup ('$SEL') en $DEST_BASE" >&2
  exit 1
fi

if [ -z "$OUT" ]; then OUT="$(mktemp -d -t vx_restore)"; else mkdir -p "$OUT"; fi

echo "archivo: $ARCHIVE"
echo "destino: $OUT"

# 1) Verificar checksum
if [ -f "$ARCHIVE.sha256" ]; then
  if ( cd "$(dirname "$ARCHIVE")" && shasum -c "$(basename "$ARCHIVE").sha256" >/dev/null 2>&1 ); then
    echo "sha256: OK"
  else
    echo "ERROR: sha256 NO coincide — archivo corrupto" >&2
    exit 2
  fi
else
  echo "WARN: sin .sha256 (integridad del contenedor no verificada)"
fi

# 2) Extraer
if ! tar -xzf "$ARCHIVE" -C "$OUT"; then
  echo "ERROR: fallo al extraer el archivo" >&2
  exit 3
fi

# 3) integrity_check de cada .db restaurada
echo "== integrity_check =="
bad=0; n=0
while IFS= read -r db; do
  n=$((n+1))
  r=$(sqlite3 "$db" "PRAGMA integrity_check;" 2>/dev/null | head -1)
  if [ "$r" != "ok" ]; then
    echo "  FALLO: ${db#"$OUT"/} -> ${r:-<error>}"
    bad=$((bad+1))
  fi
done < <(find "$OUT" -type f -name "*.db")
echo "  $((n-bad))/$n DBs íntegras"

if [ "$bad" -ne 0 ]; then
  echo "RESULTADO: restauración con $bad DB(s) corruptas" >&2
fi

echo "$OUT"
[ "$bad" -eq 0 ]
