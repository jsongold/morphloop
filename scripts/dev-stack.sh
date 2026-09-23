#!/usr/bin/env bash
# Run the local Docker stack per-worktree.
#
# Several worktrees share one repo but must not share one stack: a unique
# compose project name (containers, network, db volume) and host ports are
# derived from the worktree path, so each worktree can `up` without colliding.
# Ports land in 18000-18999 (api) and 13000-13999 (web), offset from the hash.
#
#   scripts/dev-stack.sh up [pack-path]          # build + start, then import
#   scripts/dev-stack.sh down [-v]               # stop (and delete the db)
#   scripts/dev-stack.sh logs [service]            # follow logs (api/web/db)
#   scripts/dev-stack.sh import [pack-path]        # import a pack via the api
#   scripts/dev-stack.sh test                       # e2e tests inside the api
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
HASH="$(printf '%s' "$ROOT" | shasum -a 256 | cut -c1-8)"
OFFSET="$(printf '%d' "0x${HASH:0:3}")"
export COMPOSE_PROJECT_NAME="morphloop-${HASH}"
export API_PORT="$((18000 + OFFSET % 1000))"
export WEB_PORT="$((13000 + OFFSET % 1000))"
export WEB_ORIGIN="http://localhost:${WEB_PORT}"

cmd="${1:-up}"
case "$cmd" in
  up)
    shift
    pack="${1:-contents/software-engineering}"
    docker compose up -d --build
    docker compose exec -T api python -m harness.cli import "$pack" 2>/dev/null || true
    echo "api  http://localhost:${API_PORT}  (project ${COMPOSE_PROJECT_NAME})"
    echo "web  http://localhost:${WEB_PORT}"
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