#!/usr/bin/env bash
# ============================================================
# check_sync.sh — Detecta desfase entre local y Vultr
# ============================================================
# Si la carpeta local está sucia o adelantada respecto al
# servidor, lo dice en voz alta. Sin ruido si todo está ok.
#
# Uso:
#   bash scripts/check_sync.sh           # reporte rápido
#   bash scripts/check_sync.sh --full    # reporte detallado
# ============================================================

set -uo pipefail

VECTRAX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="$HOME/.ssh/vectrax_server"
SERVER="root@140.82.28.181"
REMOTE_DIR="/opt/vectrax"
SSH_OPTS="-i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
FULL="${1:-}"

RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
BLD='\033[1m'
RST='\033[0m'

ok()   { echo -e "${GRN}✓${RST} $*"; }
warn() { echo -e "${YEL}⚠${RST}  $*"; }
fail() { echo -e "${RED}✗${RST} $*"; }
head() { echo -e "\n${BLD}$*${RST}"; }

cd "$VECTRAX_DIR"

# ── 1. Estado local (git) ─────────────────────────────────
head "LOCAL — estado git"

LOCAL_HASH=$(git rev-parse HEAD 2>/dev/null || echo "NO_GIT")
REMOTE_HASH=$(git rev-parse origin/main 2>/dev/null || echo "NO_REMOTE")
UNCOMMITTED=$(git status --porcelain 2>/dev/null | grep -c '.' || echo 0)
UNPUSHED=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l | tr -d ' ')

if [ "$UNCOMMITTED" -gt 0 ]; then
    warn "$UNCOMMITTED archivos modificados localmente sin commit"
    [ "$FULL" = "--full" ] && git --no-pager diff --stat HEAD | grep -v '^\s*$'
else
    ok "Working tree limpio"
fi

if [ "$UNPUSHED" -gt 0 ]; then
    warn "$UNPUSHED commit(s) locales no pusheados a origin/main"
    [ "$FULL" = "--full" ] && git --no-pager log origin/main..HEAD --oneline
else
    ok "Local = origin/main (sin commits adelantados)"
fi

# ── 2. Estado del servidor ────────────────────────────────
head "SERVIDOR — $SERVER"

REMOTE_GIT_HASH=""
REMOTE_DIRTY=""
SERVER_REACHABLE=false

if ssh $SSH_OPTS "$SERVER" "true" 2>/dev/null; then
    SERVER_REACHABLE=true
    REMOTE_GIT_HASH=$(ssh $SSH_OPTS "$SERVER" \
        "cd $REMOTE_DIR && git rev-parse HEAD 2>/dev/null || echo NO_GIT" 2>/dev/null || echo "ERROR")
    REMOTE_DIRTY=$(ssh $SSH_OPTS "$SERVER" \
        "cd $REMOTE_DIR && git status --porcelain 2>/dev/null | grep -c '.' || echo 0" 2>/dev/null || echo "?")
else
    fail "No se pudo conectar al servidor"
fi

if $SERVER_REACHABLE; then
    if [ "$REMOTE_GIT_HASH" = "NO_GIT" ]; then
        warn "El servidor NO tiene git inicializado en $REMOTE_DIR (solo rsync)"
    else
        ok "Git en servidor: ${REMOTE_GIT_HASH:0:10}"
    fi

    if [ "$REMOTE_DIRTY" != "0" ] && [ "$REMOTE_DIRTY" != "?" ]; then
        warn "Servidor tiene $REMOTE_DIRTY archivos modificados (cambios fuera de git)"
    fi
fi

# ── 3. Comparación ───────────────────────────────────────
head "DESFASE"

if ! $SERVER_REACHABLE; then
    fail "No se pudo comparar — servidor inaccesible"
elif [ "$REMOTE_GIT_HASH" = "NO_GIT" ]; then
    # Comparar por fecha de modificación del archivo de referencia
    LOCAL_MTIME=$(stat -f '%m' "$VECTRAX_DIR/vectrax_supervisor.py" 2>/dev/null || echo 0)
    REMOTE_MTIME=$(ssh $SSH_OPTS "$SERVER" \
        "stat -c '%Y' $REMOTE_DIR/vectrax_supervisor.py 2>/dev/null || echo 0" 2>/dev/null || echo 0)

    DIFF_SECS=$(( LOCAL_MTIME - REMOTE_MTIME ))
    DIFF_ABS=${DIFF_SECS#-}   # valor absoluto

    if [ "$DIFF_ABS" -lt 300 ]; then
        ok "Local y servidor sincronizados (diferencia < 5 min)"
    elif [ "$DIFF_SECS" -gt 0 ]; then
        warn "Local es más nuevo que servidor por $(( DIFF_SECS / 60 )) min"
        warn "→ Ejecuta: bash deploy_vultr.sh"
    else
        warn "Servidor es más nuevo que local por $(( DIFF_ABS / 60 )) min"
        warn "→ Revisa si hubo cambios en Vultr que no tienes localmente"
    fi
elif [ "$LOCAL_HASH" = "$REMOTE_GIT_HASH" ]; then
    ok "Local y servidor en el mismo commit: ${LOCAL_HASH:0:10}"
else
    # Cuántos commits de diferencia
    AHEAD=$(git log "${REMOTE_GIT_HASH}..${LOCAL_HASH}" --oneline 2>/dev/null | wc -l | tr -d ' ')
    BEHIND=$(git log "${LOCAL_HASH}..${REMOTE_GIT_HASH}" --oneline 2>/dev/null | wc -l | tr -d ' ')
    if [ "$AHEAD" -gt 0 ]; then
        warn "Local adelantado $AHEAD commit(s) respecto al servidor"
        warn "→ Ejecuta: bash deploy_vultr.sh"
    fi
    if [ "$BEHIND" -gt 0 ]; then
        warn "Servidor adelantado $BEHIND commit(s) respecto a local"
    fi
fi

# ── 4. Resumen ────────────────────────────────────────────
head "RESUMEN"

ISSUES=0
[ "$UNCOMMITTED" -gt 0 ] && ISSUES=$(( ISSUES + 1 ))
[ "$UNPUSHED" -gt 0 ] && ISSUES=$(( ISSUES + 1 ))

if [ "$ISSUES" -eq 0 ]; then
    ok "Sin desfase detectado."
else
    fail "$ISSUES problema(s) de sincronización detectados."
    echo ""
    echo "  Flujo recomendado:"
    [ "$UNCOMMITTED" -gt 0 ] && echo "    git add -A && git commit -m 'sync'"
    [ "$UNPUSHED" -gt 0 ]    && echo "    git push origin main"
    echo "    bash deploy_vultr.sh"
fi
echo ""
