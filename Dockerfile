# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

# Install dependencies first (cached separately from application code). The lock
# covers the whole uv workspace (the SDK plus apps/*), so every member's pyproject
# must be present for `--locked` to validate it.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/swe/pyproject.toml,target=apps/swe/pyproject.toml \
    uv sync --locked --no-install-workspace --no-dev

# The SDK only: apps/* stay in the context for the lock, and each app builds its
# own image (apps/swe/Dockerfile).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# The bare SDK server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn harness.api.app:app --host 0.0.0.0 --port 8000"]
