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

The software-engineering pack uses OpenAI models, so the API needs `OPENAI_API_KEY`
(chat, evaluation and learner-model updates fail without it). Keep it in an
untracked `.env.local` (`.env.*` is gitignored); Compose passes it to the API on
every `up`:

```sh
# .env.local
OPENAI_API_KEY=sk-...
```

```sh
docker compose up -d --build
```

This starts PostgreSQL, the API (it applies migrations on start) and the standard web UI.
The API starts learner lab containers on the host Docker, so Docker must be running.

Check it:

```sh
curl -s localhost:8000/health
# {"status":"ok","db":"ok"}
```

Import the pack (once per database; re-run after changing `contents/`):

```sh
docker compose exec -T api python -m harness.cli import contents/software-engineering
# status  imported
```

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
docker rm -f $(docker ps -aq --filter label=io.morphloop.lab.managed=docker_lab)  # remove leftover labs
```

### Try the DNS mission

1. Open http://localhost:3000 and choose **Start a session** (or **Resume** an earlier one).
2. Choose the DNS activity. A lab container starts and its shell appears in the terminal pane.
3. Diagnose and fix the problem in the terminal. The reset button restores the lab to its starting state.
4. Ask the tutor in the chat panel. Highlight text in the mission or visualization to quote it in the question.
5. Choose **Submit**. The evaluation result and the updated learner state appear, and **Choose next activity** starts the next round.

The timeline shows every recorded event of the session.

### End-to-end check against the real stack

With the stack running and the pack imported (the test reads the key from the shell):

```sh
set -a; . ./.env.local; set +a
uv run pytest tests/e2e -q
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
