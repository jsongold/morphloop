#!/usr/bin/env sh
# Build the SDK (sdist -> wheel), install the wheel into a throwaway venv and load the
# contracts and migrations from outside the repository: the installed package must ship
# them, and `morphloop migrate` must run against a real database with no repository
# checkout (ADR-0018 s19, #159). Usage: scripts/check-sdk-wheel.sh (needs Docker for the
# migrate check).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
container=""
cleanup() {
    [ -n "$container" ] && docker rm -f "$container" >/dev/null 2>&1
    rm -rf "$tmp"
}
trap cleanup EXIT

uv build --out-dir "$tmp/dist" "$root"
uv venv -q "$tmp/venv"
uv pip install -q --python "$tmp/venv/bin/python" "$tmp"/dist/*.whl

cd "$tmp"
env -u MORPHLOOP_CONTRACTS_DIR "$tmp/venv/bin/python" - <<'PY'
import harness
from pathlib import Path
from harness.core.contract_schemas import ContractSchemas
from harness.testing.contracts import CONTRACTS_DIR

schemas = ContractSchemas.load()
package = Path(harness.__file__).parent
assert schemas.contracts_dir == package / "contracts" == CONTRACTS_DIR, schemas.contracts_dir
assert schemas.has(ContractSchemas.id_for("schemas/pack/v2/manifest.json"))
assert schemas.event_types_v2, "no v2 event payload schemas shipped"
print("contracts shipped in the wheel:", schemas.contracts_dir)
PY

"$tmp/venv/bin/python" - <<'PY'
import harness
from pathlib import Path
from harness.adapters.postgres.migrate import locate_migrations_dir

package = Path(harness.__file__).parent
migrations_dir = locate_migrations_dir()
assert migrations_dir == package / "migrations", migrations_dir
scripts = list((migrations_dir / "versions").glob("*.py"))
assert scripts, "no migration scripts shipped"
print("migrations shipped in the wheel:", migrations_dir, f"({len(scripts)} script(s))")
PY

# `morphloop migrate` (the console script the wheel installs) against a throwaway
# Postgres, proving an app outside this repo can migrate its database with only the
# installed wheel.
container=$(docker run --rm -d \
    -e POSTGRES_USER=morphloop -e POSTGRES_PASSWORD=morphloop -e POSTGRES_DB=morphloop \
    -p 127.0.0.1::5432 postgres:16)
port=""
tries=0
while [ -z "$port" ]; do
    port=$(docker port "$container" 5432/tcp | head -1 | cut -d: -f2)
    [ -n "$port" ] && break
    tries=$((tries + 1))
    [ "$tries" -ge 60 ] && { echo "postgres container published no port" >&2; exit 1; }
    sleep 0.5
done
database_url="postgresql+psycopg://morphloop:morphloop@127.0.0.1:${port}/morphloop"

tries=0
until "$tmp/venv/bin/python" -c "
from sqlalchemy import create_engine
create_engine('$database_url', connect_args={'connect_timeout': 2}).connect().close()
" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -ge 60 ] && { echo "postgres did not become ready" >&2; exit 1; }
    sleep 0.5
done

DATABASE_URL="$database_url" "$tmp/venv/bin/morphloop" migrate

"$tmp/venv/bin/python" -c "
from sqlalchemy import create_engine, text
engine = create_engine('$database_url')
with engine.connect() as conn:
    version = conn.execute(text('select version_num from alembic_version')).scalar_one()
assert version
print('morphloop migrate ran from the installed wheel; head =', version)
"
