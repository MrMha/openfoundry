# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY blueprints/ /build/blueprints/
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 openfoundry \
    && useradd --uid 10001 --gid 10001 --create-home openfoundry
# Node/npm are available for the sandbox's allowlisted npm test command.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
WORKDIR /app
ENV UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OPENFOUNDRY_HOME=/app/.openfoundry \
    OPENFOUNDRY_WORKSPACE_ROOT=/app/workspaces \
    OPENFOUNDRY_SANDBOX_MODE=auto
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY backend/__init__.py ./backend/__init__.py
COPY backend/app/ ./backend/app/
COPY blueprints/ ./blueprints/
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca UV_SYSTEM_CERTS=true; fi; \
    uv sync --frozen --no-dev \
    && rm -rf /tmp/uv-cache \
    && mkdir -p /app/.openfoundry /app/workspaces \
    && chown -R openfoundry:openfoundry /app/.openfoundry /app/workspaces
COPY --from=frontend /build/frontend/dist/ ./frontend/dist/
# Normalize restrictive checkout permissions while keeping application code root-owned.
RUN chmod -R a+rX /app
USER openfoundry
EXPOSE 8000
VOLUME ["/app/.openfoundry", "/app/workspaces"]
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
CMD ["uv", "run", "--frozen", "--no-sync", "openfoundry", "--host", "0.0.0.0", "--port", "8000"]
