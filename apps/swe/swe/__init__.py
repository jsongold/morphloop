"""The Software Engineering app on the morphloop SDK (ADR-0018 §19, #95).

Everything here imports the SDK through :mod:`harness.sdk` only. ``PACK_DIR`` is
the SE pack this app owns (``apps/swe/pack/``); :data:`swe.app.EXTENSION` is what
the app adds to the SDK server.
"""

from pathlib import Path

PACK_DIR = Path(__file__).resolve().parents[1] / "pack"
