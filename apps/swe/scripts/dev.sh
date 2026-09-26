#!/usr/bin/env bash
# Run the SWE app stack (db / api / web) per worktree, from apps/swe.
#
# A compose project name (containers, network, db volume) and host ports are
# derived from the checkout path, so several worktrees can `up` side by side.
# Ports land in 18000-18999 (api) and 13000-13999 (web).
#
#   scripts/dev.sh up [--fake-llm]   # build + start (the api migrates the db on start)
#   scripts/dev.sh down [-v]         # stop (and delete the db)
#   scripts/dev.sh logs [service]    # follow logs (api/web/db)
#   scripts/dev.sh e2e [args...]     # Playwright E2E (web/e2e) against this stack
#
# The SE pack (apps/swe/pack) is loaded by the api at start; there is no import step.
# --fake-llm (or MORPHLOOP_LLM_PROVIDER=fake in the environment): chat / judge /
# generate answer with a deterministic fake LLM, so no key is needed. Never in production.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(git rev-parse --show-toplevel)"
HASH="$(printf '%s' "$ROOT" | shasum -a 256 | cut -c1-8)"
OFFSET="$(printf '%d' "0x${HASH:0:3}")"
export COMPOSE_PROJECT_NAME="morphloop-swe-${HASH}"
export API_PORT="$((18000 + OFFSET % 1000))"
export WEB_PORT="$((13000 + OFFSET % 1000))"

cmd="${1:-up}"
[[ $# -gt 0 ]] && shift
case "$cmd" in
  up)
    for arg in "$@"; do
      if [[ "$arg" == "--fake-llm" ]]; then
        export MORPHLOOP_LLM_PROVIDER=fake
      else
        echo "unknown option: $arg" >&2
        exit 2
      fi
    done
    docker compose up -d --build --wait
    echo "api  http://localhost:${API_PORT}  (project ${COMPOSE_PROJECT_NAME})"
    echo "web  http://localhost:${WEB_PORT}"
    ;;
  down)
    docker compose down "$@"
    ;;
  logs)
    docker compose logs -f "$@"
    ;;
  e2e)
    cd web
    E2E_BASE_URL="http://localhost:${WEB_PORT}" pnpm test:e2e "$@"
    ;;
  *)
    echo "usage: $(basename "$0") {up [--fake-llm]|down [-v]|logs [service]|e2e}" >&2
    exit 2
    ;;
esac
