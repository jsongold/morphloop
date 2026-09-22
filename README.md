# Adaptive Interactive Learning OS / Harness — Claude Code Handoff

This repository is the Harness (SDK) for an open-source domain-agnostic adaptive performance-learning engine: it provides the engine, building blocks and contracts. Content and data (subject packs and evaluation assets) live separately under `contents/<pack-id>/`, read by path rather than imported.

The v0.1 acceptance gate is a single DNS vertical slice, not the full MVP scope (`docs/decisions/0012-v01-gate-dns-slice.md`).

Start with:

1. `CLAUDE.md`
2. `docs/PRODUCT.md`
3. `docs/MVP.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. `docs/decisions/` (ADRs — authoritative where they diverge from other docs)
6. the remaining architecture documents.

## Quick Start

Requirements: Docker (with Compose v2). For local development also `uv` and `pnpm`.

### Run the whole stack

```sh
docker compose up -d --build
```

This starts PostgreSQL, the API (it applies migrations on start) and the standard web UI.

Check it:

```sh
curl -s localhost:8000/health
# {"status":"ok","db":"ok"}
```

Open http://localhost:3000 in a browser. The page shows `morphloop` and the API health (`ok` / `down` / `unreachable`).

If port 8000 or 3000 is already in use, override the host ports:

```sh
API_PORT=18000 WEB_PORT=13000 docker compose up -d --build
# then: curl -s localhost:18000/health, and open http://localhost:13000
```

Logs and shutdown:

```sh
docker compose logs -f api      # or web / db
docker compose down             # stop
docker compose down -v          # stop and delete the database volume
```

### Try the DB-down behaviour

```sh
docker compose stop db
curl -s localhost:8000/health
# {"status":"ok","db":"down"}
docker compose start db
```

### Local development

Python (API, harness):

```sh
uv sync
uv run pytest -q                 # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy harness domains      # type check
uv run lint-imports              # dependency-direction check
```

Web UI against the API running in Docker:

```sh
cd web
pnpm install
pnpm dev                         # http://localhost:3000 (NEXT_PUBLIC_API_BASE_URL defaults to http://localhost:8000)
pnpm typecheck && pnpm lint && pnpm build
```

The standard UI is optional; any UI that follows the API contract in `contracts/` can replace it.

## Core idea

```text
Assess
→ infer weakness
→ generate/select practical mission
→ learner operates real environment
→ capture actions
→ evaluate evidence
→ update learner model
→ choose next training
→ repeat
```

The first subject pack is Software Engineering.

The product is practice-first: terminal/editor/browser/database activities are primary. Text, diagrams and animations exist to explain or remediate, not to replace practice.

## Key UX rule

Every abstraction should have a path to the actual implementation and observable system state:

`abstract → mechanism → implementation → observation → operation`
