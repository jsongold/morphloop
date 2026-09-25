#!/usr/bin/env sh
# Build the SDK (sdist -> wheel), install the wheel into a throwaway venv and load the
# contracts from outside the repository: the installed package must ship them
# (ADR-0018 s19). Usage: scripts/check-sdk-wheel.sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

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
