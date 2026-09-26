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

Requirements: Docker (with Compose v2). For local development also `uv`.

### The SWE app (web UI, labs, the SE pack)

The SWE app lives in its own repository: https://github.com/jsongold/browncircle.
UIs live in app repositories; this repository is the backend SDK (plus `examples/quickstart`).

### Run the SDK stack

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

This starts PostgreSQL and the bare SDK API (it applies migrations on start); no web UI.
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

If port 8000 is already in use, override the host port:

```sh
API_PORT=18000 docker compose up -d --build
# then: curl -s localhost:18000/health
```

### Multiple worktrees (recommended for concurrent development)

This repo is developed across several worktrees. Never run `docker compose`
directly there: every worktree would reuse the same container names, network,
db volume and host ports. Use the per-worktree wrapper, which derives a unique
compose project name (`morphloop-<hash>`) and host port (api 17000+) from
the worktree path:

```sh
./scripts/dev-stack.sh up        # build + start + import the SE pack
# api  http://localhost:17xxx   (project morphloop-2efd6458)
./scripts/dev-stack.sh logs api  # follow logs (api / db)
./scripts/dev-stack.sh import    # (re)import the pack
./scripts/dev-stack.sh test      # e2e tests in the api container
./scripts/dev-stack.sh down -v   # stop and delete the db volume
```

Another worktree's stack is reached with `docker compose -p morphloop-<hash> ...`.

Logs and shutdown:

```sh
docker compose logs -f api      # or db
docker compose down             # stop
docker compose down -v          # stop and delete the database volume
docker rm -f $(docker ps -aq --filter label=io.morphloop.lab.managed=docker_lab)  # remove leftover labs
```

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
uv run mypy                      # type check
uv run lint-imports              # dependency-direction check
```

Any UI that follows the API contract in `contracts/` can run against the API; UIs live in app repositories.

### Quick start: the SDK alone

`examples/quickstart/` is the smallest possible app on the SDK: one file
(`app.py`) that calls `harness.sdk.create_app()` and points it at a tiny pack
(`pack/`, one topic, one textbook doc, one choice drill) — no web UI, no labs,
no app extension. It needs a Postgres to talk to (the event store, ADR-0008);
a throwaway container is enough.

```sh
uv sync

docker run -d --name morphloop-quickstart-db \
  -e POSTGRES_USER=morphloop -e POSTGRES_PASSWORD=morphloop -e POSTGRES_DB=morphloop \
  -p 5432:5432 postgres:16
export DATABASE_URL=postgresql+psycopg://morphloop:morphloop@localhost:5432/morphloop
uv run alembic upgrade head

uv run uvicorn --app-dir examples/quickstart app:app --port 8000
```

In another shell:

```sh
curl -s localhost:8000/v2/topics
# {"pack_id":"quickstart","pack_hash":"sha256:...","topics":[{"id":"basics", ...}]}

curl -s -X POST localhost:8000/v2/sessions \
  -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"pack_id": "quickstart", "topic_id": "basics"}'
# {"id":"ses_...","pack_id":"quickstart","topic_id":"basics", ...}

curl -s localhost:8000/v2/drills
# {"items":[{"id":"choice","question":"Which choice is correct?", ...}]}
```

Stop the server (Ctrl-C) and remove the throwaway database when done:
`docker rm -f morphloop-quickstart-db`. `tests/test_quickstart.py` runs the
same three calls against the example app with the in-memory `/v2` fakes
(`harness.testing`), so CI catches a break with no database needed.

### Building an app on the SDK

The SDK owns the `Artifact` base and the extension points; concrete artifact
types (lab, diagram, ...) belong to an app (its own repository, ADR-0018 §19). An app
is its own package that depends on the installed SDK package (`morphloop`) and
imports `harness.sdk` only; nothing else in `harness` is public API.

```sh
uv build                                    # dist/morphloop-*.whl, contracts included
uv pip install dist/morphloop-*.whl         # in the app's own environment, or
uv add morphloop                            # once the SDK is published
scripts/check-sdk-wheel.sh                  # proves an installed wheel loads its contracts
```

The wheel ships `contracts/` as package data (`harness/contracts/`), so
`ContractSchemas.load()` and `harness.testing` need no repository checkout;
`MORPHLOOP_CONTRACTS_DIR` still overrides it. The SDK's own tests use only the
minimal fixture pack under `tests/contracts/fixtures/pack-v2/valid/dns-pack/` with
test-only stub types; a real pack, its domain adapters and its tests belong to the app
(the SWE app: https://github.com/jsongold/browncircle).

An app imports `harness.sdk` only:

```python
from harness.sdk import AppExtension, Artifact, create_app


class LabArtifact(Artifact):
    type = "lab"  # what a pack artifact's `type` names
    spec_schema = {...}  # JSON Schema (draft 2020-12) of its `spec`

    @classmethod
    def validate_spec(cls, spec, pack):  # optional cross-file rules
        return []  # one message per problem


app = create_app(extensions=[AppExtension(routers=(router,), artifact_types=(LabArtifact,))])
```

Routers mount under `/v2`. The pack importer accepts only the registered types and
validates every artifact `spec` with the type's schema and validator; a pack that
names another type is refused. `harness.sdk` also exports what
an artifact type with routes needs (the v2 event store Port and value types, the
`View` base, the `/v2` request dependencies, `problem()`, the lab and terminal
Ports with their Docker adapters, the domain adapter registry); test support
(fakes, the in-memory store, contract validation) is `harness.testing`.

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
