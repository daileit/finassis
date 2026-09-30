# syntax=docker/dockerfile:1.7
# Build context is the REPO ROOT (the image needs seeds/ and i18n/):
#   docker build -f api/Dockerfile -t finassis-api .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

# ---- build: resolve and install the package into a venv ----
FROM base AS build
WORKDIR /build
COPY api/pyproject.toml api/README.md ./
COPY api/src ./src
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python .

# ---- runtime ----
FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    FINASSIS_API_DIR=/app/api \
    FINASSIS_SEEDS_DIR=/app/seeds \
    FINASSIS_I18N_DIR=/app/i18n \
    FINASSIS_SCHEMA_SQL=/app/api/db/schema.sql \
    FINASSIS_CONFIG_YAML=/app/api/config.yaml
RUN groupadd -r finassis && useradd -r -g finassis -d /app finassis
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# runtime needs alembic/ (migrations), db/ (schema.sql), config.yaml; seeds and i18n are shared repo data
COPY api/alembic.ini api/config.yaml /app/api/
COPY api/alembic /app/api/alembic
COPY api/db /app/api/db
COPY seeds /app/seeds
COPY i18n /app/i18n
RUN chown -R finassis:finassis /app
USER finassis
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).status==200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--", "finassis"]
CMD ["api"]
