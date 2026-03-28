# ── Stage 1: test (used by CI) ────────────────────────────────────
FROM python:3.13-slim AS test

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY . .
# CI runs: ruff check . && pytest

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Node.js 22 (for Copilot CLI + MCP extension)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copilot CLI
RUN npm install -g @github/copilot

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# MCP extension deps
COPY olx-db-ext/package.json olx-db-ext/package-lock.json olx-db-ext/
RUN cd olx-db-ext && npm ci --omit=dev

# Application code
COPY . .

# Non-root user
RUN useradd -r -m -s /bin/false appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

# Health check: verify main process is running
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD python -c "import os, signal; os.kill(1, signal.SIG_DFL) or True" || exit 1

CMD ["python", "main.py"]