# =============================================================
# Vectrax Platform — Multi-stage Dockerfile
# =============================================================

# --- Stage 1: Builder ---
FROM python:3.9-slim AS builder

WORKDIR /build

COPY pyproject.toml setup.py ./
COPY requirements.txt* ./

RUN pip install --no-cache-dir --upgrade pip && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi && \
    pip install --no-cache-dir pydantic uvicorn fastapi httpx

# --- Stage 2: Runtime ---
FROM python:3.9-slim AS runtime

# Create non-root user
RUN groupadd -r vectrax && useradd -r -g vectrax -m vectrax

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/vault /app/logs /app/reports /app/config/env && \
    chown -R vectrax:vectrax /app

ENV PYTHONPATH=/app
ENV VX_ENV=production
EXPOSE 8900

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/v1/health')" || exit 1

USER vectrax

CMD ["python", "-m", "services.runtime"]
