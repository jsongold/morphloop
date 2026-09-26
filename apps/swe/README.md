# SWE app (`apps/swe`)

The Software Engineering app on the morphloop SDK: the `lab` and `diagram`
artifact types, the SE pack (`pack/`) and the web UI (`web/`). Run everything
from this directory.

Requirements: Docker (Compose v2). For the E2E also `pnpm` (Node 22).

## Run

```sh
cd apps/swe
./scripts/dev.sh up              # real LLM (key from .env.local)
./scripts/dev.sh up --fake-llm   # deterministic fake LLM, no key needed
# api  http://localhost:18xxx  (project morphloop-swe-<hash>)
# web  http://localhost:13xxx
curl -s localhost:18xxx/v2/topics
./scripts/dev.sh logs api        # follow logs (api / web / db)
./scripts/dev.sh down -v         # stop and delete the db volume
```

`up` builds and starts db / api / web and waits until they are healthy. The api
runs `morphloop migrate` (migrations shipped in the SDK) on every start and loads the SE pack from `pack/`
(`MORPHLOOP_PACK_V2_DIR` overrides it); there is no separate import step. The
compose project name and host ports are derived from the checkout path, so
several worktrees can run side by side. Plain `docker compose up -d --build`
works too (ports 8000 / 3000, override with `API_PORT` / `WEB_PORT`).

The api starts learner lab containers on the host Docker, so it mounts the host
Docker socket: this is a local stack, not a hardened deployment.

## Environment

LLM keys go in `apps/swe/.env.local` (gitignored, never copied into an image),
read by the api on every `up`:

```sh
# apps/swe/.env.local
OPENAI_API_KEY=sk-...
```

| Variable | Where | Meaning |
|---|---|---|
| `OPENAI_API_KEY` (or another litellm key) | `.env.local` | the pack's LLM provider key |
| `MORPHLOOP_LLM_PROVIDER=fake` | shell (`--fake-llm` sets it) | fake LLM, no key; refused when `MORPHLOOP_ENVIRONMENT=production` |
| `DOCKER_HOST` | shell | Docker endpoint for labs, passed through when set |
| `API_PORT` / `WEB_PORT` | shell (`dev.sh` sets them) | host ports |
| `MORPHLOOP_PACK_V2_DIR` | `.env.local` | serve another pack instead of `pack/` |

## E2E

Against a running stack (`--fake-llm`, so the chat steps need no key):

```sh
./scripts/dev.sh up --fake-llm
(cd web && pnpm install && pnpm exec playwright install chromium)   # once
./scripts/dev.sh e2e             # = E2E_BASE_URL=http://localhost:<web-port> pnpm test:e2e
./scripts/dev.sh down -v
```

## Tests and checks

From the SDK repository root (the app is a uv workspace member there):
`uv run pytest -q apps/swe/tests`, `uv run mypy harness domains apps`,
`uv run lint-imports`. Web: `cd web && pnpm lint && pnpm typecheck && pnpm test`.

## Dependency on the SDK

The app depends on the SDK package `morphloop` (`pyproject.toml`) and imports
`harness.sdk` only. Inside the SDK repository that is a uv workspace dependency,
so the api image is built with the SDK repo root as context
(`docker-compose.yml`: `context: ../..`) and also takes the SDK's migrations
from there. When the app moves to its own repository:

1. in `pyproject.toml`, replace `morphloop = { workspace = true }` with
   `morphloop = { git = "https://github.com/jsongold/morphloop", tag = "..." }`
   (or drop the source and pin a published version), then `uv lock`;
2. set the api build context to `.` and copy only this app's files in the Dockerfile;
3. run the migrations shipped with the SDK instead of the repo-root `migrations/`
   (the SDK wheel does not ship them yet).
