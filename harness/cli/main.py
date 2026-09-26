"""The ``morphloop`` command line: ``rebuild``, ``migrate`` (ADR-0017).

``pyproject.toml`` registers ``morphloop`` as a console script (``[project.scripts]``),
so an installed SDK gets a ``morphloop`` executable. It can also be run as a module::

    uv run python -m harness.cli rebuild
    uv run python -m harness.cli migrate

Exit codes: ``0`` success, ``1`` a reported failure
(:class:`~harness.cli.errors.CommandError`), ``2`` a usage error from typer.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

import typer
from typer.exceptions import TyperException

from harness.adapters.postgres.migrate import migrate
from harness.cli import rebuild, wiring
from harness.cli.errors import CommandError

PROG = "python -m harness.cli"

app = typer.Typer(
    name=PROG,
    add_completion=False,
    help="Maintain a morphloop deployment.",
)


@app.callback()
def commands() -> None:
    """Keep ``rebuild`` a named subcommand (typer runs a lone command without its name)."""


@app.command(
    "rebuild",
    short_help="rebuild the v0.2 views from the event log",
    help="Rebuild every registered v0.2 view from the stored events (DATABASE_URL).",
)
def rebuild_cmd() -> int:
    with wiring.event_store_v2() as store_v2:
        replayed = rebuild.rebuild(store_v2)
    print(rebuild.format_result(replayed))
    return 0


@app.command(
    "migrate",
    short_help="upgrade the database at DATABASE_URL to head",
    help=(
        "Run the alembic migrations shipped inside this package (#159) against "
        "MORPHLOOP_DATABASE_DIRECT_URL (else DATABASE_URL), up to 'head'. Needs no "
        "repository checkout, so an app that only depends on the installed SDK can "
        "migrate its own database."
    ),
)
def migrate_cmd() -> int:
    migrate(wiring.database_url())
    print("migrated      head")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        return int(app(args, standalone_mode=False))
    except TyperException as exc:
        # Usage errors (e.g. click's UsageError/MissingParameter) carry a
        # ``.show()`` that prints "Usage: ...\nError: ..." to stderr, matching
        # argparse's prior usage-error output; ``.exit_code`` is 2 for those.
        exc.show()  # type: ignore[attr-defined]
        return exc.exit_code
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
