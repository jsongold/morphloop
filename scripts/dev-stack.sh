#!/usr/bin/env bash
# Run the SDK Docker stack (db + bare SDK api) per-worktree. Apps (with their web
# UIs) run from their own repositories.
#
# Several worktrees share one repo but must not share one stack: a unique
# compose project name (containers, network, db volume) and host ports are
# derived from the worktree path, so each worktree can `up` without colliding.
# The api port lands in 17000-17999, offset from the hash.
#
#   scripts/dev-stack.sh up [pack-path] [--fake-llm]  # build + start, then import
#   scripts/dev-stack.sh down [-v]               # stop (and delete the db)
#   scripts/dev-stack.sh logs [service]            # follow logs (api/db)
#   scripts/dev-stack.sh import [pack-path]        # import a pack via the api
#   scripts/dev-stack.sh test                       # e2e tests inside the api
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
    pack="${args[0]:-contents/software-engineering}"
    docker compose up -d --build
    docker compose exec -T api python -m harness.cli import "$pack" 2>/dev/null || true
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
  import)
    shift
    docker compose exec -T api python -m harness.cli import "${1:-contents/software-engineering}"
    ;;
  test)
    shift
    docker compose exec -T api pytest tests/e2e "$@"
    ;;
  *)
    echo "usage: $(basename "$0") {up|down|logs|import|test}" >&2
    exit 2
    ;;
esac