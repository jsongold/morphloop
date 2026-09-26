"""Run the packaged alembic migrations against ``DATABASE_URL`` (#159).

The migration scripts live at the repository root (``migrations/``) so the
existing dev/CI flow (``alembic upgrade head`` against the root
``alembic.ini``) keeps working unchanged. ``[tool.hatch.build.targets.wheel]``
in ``pyproject.toml`` ships the same directory inside the wheel at
``harness/migrations`` (the same trick already used for ``contracts/``), so an
app that only depends on the installed SDK -- no repository checkout -- can
still migrate its database, via :func:`migrate` or ``morphloop migrate``.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config


class MigrationsNotFoundError(RuntimeError):
    """No usable ``migrations/`` directory could be located."""


def locate_migrations_dir() -> Path:
    """Return the migrations directory: the packaged copy, else the checkout's."""
    packaged = files("harness") / "migrations"
    if isinstance(packaged, Path) and (packaged / "versions").is_dir():
        return packaged
    for parent in Path(__file__).resolve().parents:
        if (parent / "migrations" / "versions").is_dir():
            return parent / "migrations"
    raise MigrationsNotFoundError(f"no migrations/versions/ above {Path(__file__).resolve()}")


def migrate(database_url: str) -> None:
    """Upgrade the database at ``database_url`` to ``head``.

    ``migrations/env.py`` reads the ``DATABASE_URL`` environment variable in
    preference to the config, so it is set for the duration of the call
    (restored afterwards) rather than duplicating that lookup here.
    """
    config = Config()
    config.set_main_option("script_location", str(locate_migrations_dir()))
    config.set_main_option("sqlalchemy.url", database_url)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
