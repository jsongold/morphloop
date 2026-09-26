#!/usr/bin/env bash
# Run the SDK Docker stack (db + bare SDK api) per-worktree. Apps (with their web
# UIs) run from their own repositories.
#
# Several worktrees share one repo but must not share one stack: a unique
# compose project name (containers, network, db volume) and host ports are
# derived from the worktree path, so each worktree can `up` without colliding.
# The api port lands in 17000-17999, offset from the hash.
#
#   scripts/dev-stack.sh up [--fake-llm]            # build + start (SDK api only)
#   scripts/dev-stack.sh down [-v]               # stop (and delete the db)
#   scripts/dev-stack.sh logs [service]            # follow logs (api/db)
#
# --fake-llm (or MORPHLOOP_LLM_PROVIDER=fake in the environment, #130): the api
# answers chat / judge / generate with a deterministic fake LLM instead of a
# real provider, so the stack works with no key. Never set this in production.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
HASH="$(printf '%s' "$ROOT" | shasum -a 256 | cut -c1-8)"
OFFSET="$(printf '%d' "0x${HASH:0:3}")"
export COMPOSE_PROJECT_NAME="morphloop-${HASH}"
export API_PORT="$((17000 + OFFSET % 1000))"

cmd="${1:-up}"
case "$cmd" in
  up)
    shift
    args=()
    for arg in "$@"; do
      if [[ "$arg" == "--fake-llm" ]]; then
        export MORPHLOOP_LLM_PROVIDER=fake
      else
        args+=("$arg")
      fi
    done
    docker compose up -d --build
    echo "api  http://localhost:${API_PORT}  (project ${COMPOSE_PROJECT_NAME})"
    ;;
  down)
    shift
    docker compose down "$@"
    ;;
  logs)
    shift
    docker compose logs -f "$@"
    ;;
  *)
    echo "usage: $(basename "$0") {up|down|logs}" >&2
    exit 2
    ;;
esac