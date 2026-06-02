# ============================================
# Vectrax Production Dockerfile
# ============================================
# Runs 4 processes via supervisor:
#   - Telegram Gateway (polling + fast-path)
#   - Pipeline Worker (heavy processing)
#   - Core API (FastAPI on port 8900)
#   - Meta Loop (cognitive cycles)
#
# Usage:
#   docker-compose up -d
# ============================================

FROM python:3.11-slim

# git: requerido por core/self_observation/deployment_memory.py para
#   leer commits + ramas + diffs y exponerlos al CREATOR MODE.
# sqlite3 (CLI): útil para diagnóstico runtime de las DBs persistentes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git sqlite3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.prod.txt .
RUN pip install --no-cache-dir -r requirements.prod.txt

COPY . .

# El rsync del deploy excluye .git/, así que en prod no había git
# repo en /app. Forzamos un snapshot mínimo: si .git no existe en
# build context, escribimos head/branch a /app/.git_snapshot que el
# deployment_memory puede leer como fallback.
RUN if [ -d /app/.git ]; then \
        echo "[build] git repo present in image"; \
    else \
        mkdir -p /app/.git_snapshot && \
        echo "snapshot-only" > /app/.git_snapshot/MARKER; \
        echo "[build] no .git — created /app/.git_snapshot fallback"; \
    fi

RUN mkdir -p /app/vault /root/.vectrax \
    && ln -sfn /app /root/Vectrax

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV VECTRAX_VAULT_DIR=/app/vault

EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python /app/vectrax_supervisor.py --check || exit 1

CMD ["python", "/app/vectrax_supervisor.py"]
