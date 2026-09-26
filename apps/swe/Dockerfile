# syntax=docker/dockerfile:1
# The SWE api: the SDK server with this app's extension (swe.app.create_swe_app).
#
# Build context: while this app is a uv workspace member of the SDK repository, the
# context is the SDK repo root (docker-compose.yml: `context: ../..`), because the
# lock, the SDK sources and the migrations live there. Once apps/swe moves to its
# own repository, `morphloop` becomes a git/registry dependency in pyproject.toml
# (`[tool.uv.sources] morphloop = { git = "..." }` or a published version), the
# context becomes `.`, and the COPY lines below shrink to this app's own files.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

# Third-party dependencies first (cached separately from the sources). `--locked`
# validates the whole workspace lock, so every member's pyproject must be present.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/swe/pyproject.toml,target=apps/swe/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package morphloop-swe

# The SDK (package `morphloop`) and its migrations, then this app with its pack.
# `morphloop migrate` (below) builds its own alembic Config, so alembic.ini is not needed here.
COPY pyproject.toml uv.lock README.md ./
COPY harness harness
COPY domains domains
COPY contracts contracts
COPY migrations migrations
COPY apps/swe/pyproject.toml apps/swe/pyproject.toml
COPY apps/swe/swe apps/swe/swe
COPY apps/swe/pack apps/swe/pack
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package morphloop-swe

FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["sh", "-c", "morphloop migrate && uvicorn --factory swe.app:create_swe_app --host 0.0.0.0 --port 8000"]
