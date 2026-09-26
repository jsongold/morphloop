"""SDK quickstart (#150): the bare `/v2` server with no app extension, serving
the tiny generic pack under `pack/` next to this file.

Run it (from the repo root, with a reachable `DATABASE_URL`, migrated):

    uv run uvicorn --app-dir examples/quickstart app:app --port 8000

`MORPHLOOP_PACK_V2_DIR`, if set, overrides the bundled pack -- the same
override any `harness.sdk` app respects (ADR-0018 s19).
"""

from __future__ import annotations

import os
from pathlib import Path

from harness.sdk import create_app, import_pack_v2

PACK_DIR = Path(__file__).parent / "pack"

app = create_app()
if not os.environ.get("MORPHLOOP_PACK_V2_DIR"):
    app.state.pack_v2 = import_pack_v2(PACK_DIR)
