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

### Multiple worktrees (recommended for concurrent development)

This repo is developed across several worktrees. Never run `docker compose`
directly there: every worktree would reuse the same container names, network,
db volume and host ports. Use the per-worktree wrapper, which derives a unique
compose project name (`morphloop-<hash>`) and host ports (api 18000+,
web 13000+) from the worktree path:

```sh
./scripts/dev-stack.sh up        # build + start + import the SE pack
# api  http://localhost:18xxx   (project morphloop-2efd6458)
# web  http://localhost:13xxx
./scripts/dev-stack.sh logs api  # follow logs (api / web / db)
./scripts/dev-stack.sh import    # (re)import the pack
./scripts/dev-stack.sh test      # e2e tests in the api container
./scripts/dev-stack.sh down -v   # stop and delete the db volume
```

Another worktree's stack is reached with `docker compose -p morphloop-<hash> ...`.

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
cd apps/swe/web
pnpm install
pnpm dev                         # http://localhost:3000 (NEXT_PUBLIC_API_BASE_URL defaults to http://localhost:8000)
pnpm typecheck && pnpm lint && pnpm build
```

The standard UI is optional; any UI that follows the API contract in `contracts/` can replace it.

### Building an app on the SDK

The SDK owns the `Artifact` base and the extension points; concrete artifact
types (lab, diagram, ...) belong to an app (`apps/<app>/`, ADR-0018 §19). An app
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
`MORPHLOOP_CONTRACTS_DIR` still overrides it. Inside this repository the root
`pyproject.toml` is a uv workspace whose members are `apps/*`: an app under
`apps/<app>/` with its own `pyproject.toml` (depending on `morphloop`) and tests
is picked up by `uv sync`, and later moves out to its own repository unchanged.
The SDK's own tests use only the minimal fixture pack under
`tests/contracts/fixtures/pack-v2/valid/dns-pack/`; a real pack and its tests
belong to the app.

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
names another type is refused. `harness must not import apps` is enforced by
`lint-imports`.

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
