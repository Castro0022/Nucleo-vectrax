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

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.prod.txt .
RUN pip install --no-cache-dir -r requirements.prod.txt

COPY . .

RUN mkdir -p /app/vault /root/.vectrax

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python /app/vectrax_supervisor.py --check || exit 1

CMD ["python", "/app/vectrax_supervisor.py"]
