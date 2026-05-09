#!/usr/bin/env bash
# ============================================
# Vectrax → Vultr Deploy (One-Shot)
# ============================================
# Sube, instala, y deja corriendo Vectrax
# Uso: bash deploy_vultr.sh
# ============================================

set -euo pipefail

SSH_KEY="$HOME/.ssh/vectrax_server"
SERVER="root@140.82.28.181"
REMOTE_DIR="/opt/vectrax"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_OPTS="-i $SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

echo ""
echo "=========================================="
echo "  VECTRAX DEPLOY → Vultr"
echo "=========================================="
echo "  Servidor: $SERVER"
echo "  Destino:  $REMOTE_DIR"
echo "=========================================="
echo ""

# ------------------------------------------
# 1. Subir proyecto al servidor
# ------------------------------------------
echo "[1/4] Subiendo proyecto al servidor..."
# IMPORTANT: .env is NEVER rsynced from local. Runtime config is managed
# manually on the server (or via a secrets manager later). Sync’ing .env
# caused Apr-22 outage by overwriting server USE_WEBHOOK=0 with local=1.
# Also excluding vault/ data/ logs/ so cognitive + operational data stay
# local to the server (they are large and owned by the runtime).
# Incluir .git/ deliberadamente: el container necesita git para que
# core/self_observation/deployment_memory.py pueda leer commits/branches
# y exponerlos en el CREATOR MODE. Sin .git, el bloque [PERCEPCIÓN
# OPERACIONAL] sale con commits vacíos.
rsync -azP --delete \
    -e "ssh $SSH_OPTS" \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='*.db-shm' \
    --exclude='*.db-wal' \
    --exclude='*.db' \
    --exclude='node_modules' \
    --exclude='vault/' \
    --exclude='data/' \
    --exclude='logs/' \
    --exclude='.DS_Store' \
    "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/"

echo "    ✓ Proyecto subido"

# ------------------------------------------
# 2. Instalar Docker (si no existe) + deps
# ------------------------------------------
echo ""
echo "[2/4] Configurando servidor..."
ssh $SSH_OPTS "$SERVER" bash -s <<'REMOTE_SETUP'
set -euo pipefail

echo "    → Verificando Docker..."
if ! command -v docker &>/dev/null; then
    echo "    → Instalando Docker..."
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-plugin >/dev/null 2>&1
    systemctl enable docker
    systemctl start docker
    echo "    ✓ Docker instalado"
else
    echo "    ✓ Docker ya instalado ($(docker --version | cut -d' ' -f3))"
fi

# Ensure docker compose works (v2 plugin or standalone)
if docker compose version &>/dev/null; then
    echo "    ✓ Docker Compose v2 disponible"
elif command -v docker-compose &>/dev/null; then
    echo "    ✓ Docker Compose standalone disponible"
else
    echo "    → Instalando docker-compose..."
    apt-get install -y -qq docker-compose >/dev/null 2>&1
    echo "    ✓ docker-compose instalado"
fi

echo "    → Preparando directorios..."
mkdir -p /opt/vectrax/vault
mkdir -p /root/.vectrax

echo "    ✓ Servidor configurado"
REMOTE_SETUP

# ------------------------------------------
# 3. Build y arranque con Docker
# ------------------------------------------
echo ""
echo "[3/4] Construyendo y arrancando Vectrax..."
ssh $SSH_OPTS "$SERVER" bash -s <<'REMOTE_START'
set -euo pipefail

cd /opt/vectrax

# Detener instancia anterior si existe
echo "    → Deteniendo instancia anterior (si existe)..."
if docker compose ps -q 2>/dev/null | grep -q .; then
    docker compose down 2>/dev/null || true
elif docker-compose ps -q 2>/dev/null | grep -q .; then
    docker-compose down 2>/dev/null || true
fi

# Build + start (uses cache — only rebuilds changed layers)
echo "    → Construyendo imagen Docker..."
if docker compose version &>/dev/null; then
    docker compose build
    echo "    → Arrancando servicios..."
    docker compose up -d
else
    docker-compose build
    echo "    → Arrancando servicios..."
    docker-compose up -d
fi

echo "    ✓ Vectrax arrancado"
REMOTE_START

# ------------------------------------------
# 4. Verificar que está corriendo
# ------------------------------------------
echo ""
echo "[4/4] Verificando estado..."
sleep 5
ssh $SSH_OPTS "$SERVER" bash -s <<'REMOTE_CHECK'
cd /opt/vectrax

echo ""
echo "=========================================="
echo "  ESTADO DEL DEPLOY"
echo "=========================================="

# Container status
if docker compose version &>/dev/null; then
    docker compose ps
else
    docker-compose ps
fi

echo ""

# Logs últimas líneas
echo "--- Últimos logs ---"
if docker compose version &>/dev/null; then
    docker compose logs --tail=15
else
    docker-compose logs --tail=15
fi

echo ""
echo "=========================================="
echo "  ✓ VECTRAX CORRIENDO EN VULTR"
echo "  IP: 140.82.28.181"
echo "  Contenedor: vectrax-core"
echo "  Restart: unless-stopped (persistente)"
echo "=========================================="
echo ""
echo "  Comandos útiles:"
echo "    Ver logs:    ssh root@140.82.28.181 'cd /opt/vectrax && docker compose logs -f'"
echo "    Reiniciar:   ssh root@140.82.28.181 'cd /opt/vectrax && docker compose restart'"
echo "    Detener:     ssh root@140.82.28.181 'cd /opt/vectrax && docker compose down'"
echo "    Estado:      ssh root@140.82.28.181 'cd /opt/vectrax && docker compose ps'"
echo ""
REMOTE_CHECK

echo ""
echo "Deploy completado."
