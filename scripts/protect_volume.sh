#!/usr/bin/env bash
# =============================================================================
# scripts/protect_volume.sh — Backup del volumen vectrax-runtime
#
# Uso:
#   bash scripts/protect_volume.sh          # backup antes de operaciones peligrosas
#   bash scripts/protect_volume.sh restore  # restaurar último backup
#
# El volumen vectrax_vectrax-runtime contiene:
#   vectrax.db        — estrellas, user_stars, patrones, constelaciones
#   continuity.db     — idempotencia Telegram
#   anti_repetition.db
#   sovereignty.jsonl  — audit log
#   gateway/worker heartbeats y logs
#
# ⚠️  `docker compose down -v` DESTRUYE este volumen irreversiblemente.
#     Ejecutar este script ANTES de cualquier operación destructiva.
# =============================================================================

set -euo pipefail

VOLUME_NAME="vectrax_vectrax-runtime"
BACKUP_DIR="${HOME}/vectrax_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/runtime_${TIMESTAMP}.tar.gz"

mkdir -p "${BACKUP_DIR}"

# ── RESTORE mode ──────────────────────────────────────────────────────────
if [[ "${1:-}" == "restore" ]]; then
    LATEST=$(ls -t "${BACKUP_DIR}"/runtime_*.tar.gz 2>/dev/null | head -1)
    if [[ -z "${LATEST}" ]]; then
        echo "❌ No backups found in ${BACKUP_DIR}"
        exit 1
    fi
    echo "🔄 Restoring from: ${LATEST}"
    echo "   This will OVERWRITE current volume data."
    read -p "   Continue? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
    # Stop container, restore, restart
    docker compose -f /opt/vectrax/docker-compose.yml down 2>/dev/null || true
    docker run --rm \
        -v "${VOLUME_NAME}:/data" \
        -v "${LATEST}:/backup.tar.gz:ro" \
        alpine sh -c "rm -rf /data/* && tar xzf /backup.tar.gz -C /data"
    echo "✅ Restored. Run 'docker compose up -d' to restart."
    exit 0
fi

# ── BACKUP mode (default) ────────────────────────────────────────────────

# Verify volume exists
if ! docker volume inspect "${VOLUME_NAME}" &>/dev/null; then
    echo "❌ Volume ${VOLUME_NAME} not found."
    echo "   Are you on the production server?"
    exit 1
fi

echo "📦 Backing up ${VOLUME_NAME}..."
docker run --rm \
    -v "${VOLUME_NAME}:/data:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar czf "/backup/runtime_${TIMESTAMP}.tar.gz" -C /data .

SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "✅ Backup saved: ${BACKUP_FILE} (${SIZE})"

# Keep only last 5 backups
ls -t "${BACKUP_DIR}"/runtime_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
TOTAL=$(ls "${BACKUP_DIR}"/runtime_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
echo "   Retained ${TOTAL} backups in ${BACKUP_DIR}"
