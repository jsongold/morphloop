"""The ``morphloop`` command line: ``generate``, ``import``, ``rebuild`` (ADR-0017).

Run it as a module (``pyproject.toml`` declares no console script)::

    uv run python -m harness.cli generate contents/software-engineering \\
        --template diagnose-dns-resolver-misconfiguration
    uv run python -m harness.cli import contents/software-engineering
    uv run python -m harness.cli rebuild

Exit codes: ``0`` success, ``1`` a reported failure
(:class:`~harness.cli.errors.CommandError`), ``2`` a usage error from typer.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Annotated

import typer
from typer.exceptions import TyperException

from harness.cli import import_pack, rebuild, wiring
from harness.cli.errors import CommandError
from harness.cli.generate import ActivityGenerator, GenerateRequest
from harness.cli.pack_files import FilesystemPackWriter
from harness.core.loop import utc_now

PROG = "python -m harness.cli"

app = typer.Typer(
    name=PROG,
    add_completion=False,
    help="Author, import and maintain morphloop content.",
)

PackArg = Annotated[str, typer.Argument(help="path to the pack directory, e.g. contents/<pack-id>")]


@app.command(
    "generate",
    short_help="run the authoring generation path for one Template and write the result",
    help=(
        "Generate one activity from a Template with the pack's Generator, validate it "
        "(schema, adapter references, and a real lab in which the checks must fail "
        "before and pass after the reference solution), then write the finalized "
        "Definition files and the generation record into the pack."
    ),
)
def generate(
    pack: PackArg,
    template: Annotated[str, typer.Option(help="id of the activity_template to run")],
    activity_id: Annotated[
        str | None,
        typer.Option(
            "--activity-id",
            help="id for the generated activity (default: the next free gen-<template>-NNN)",
        ),
    ] = None,
    max_attempts: Annotated[
        int | None,
        typer.Option(
            "--max-attempts",
            help="candidates to try (default: the pack's max_regenerations + 1)",
        ),
    ] = None,
) -> int:
    schemas = wiring.contract_schemas()
    adapters = wiring.domain_adapters()
    generator = ActivityGenerator(
        source=wiring.pack_source(),
        writer=FilesystemPackWriter(),
        schemas=schemas,
        adapters=adapters,
        llm=wiring.llm_provider(),
        lab=wiring.lab_runtime(),
        importer_for=wiring.importer_factory(
            store=wiring.UnusedEventStore(), schemas=schemas, adapters=adapters
        ),
        now=utc_now,
    )
    result = generator.run(
        GenerateRequest(
            location=pack,
            template_id=template,
            activity_id=activity_id,
            max_attempts=max_attempts,
        )
    )
    print(f"activity      {result.activity_id}")
    print(f"attempts      {result.attempts}")
    for candidate in result.rejected:
        print(f"  rejected #{candidate.attempt} [{candidate.failed_step}] {candidate.reason}")
    for path in result.written:
        print(f"wrote         {path}")
    return 0


@app.command(
    "import",
    short_help="validate a pack and write its projection to the database",
    help=(
        "Parse, validate and hash the pack at PACK and write the pack projection "
        "through the event store (DATABASE_URL). Importing unchanged content again "
        "writes nothing."
    ),
)
def import_(pack: PackArg) -> int:
    schemas = wiring.contract_schemas()
    adapters = wiring.domain_adapters()
    with wiring.event_store() as store:
        importer = wiring.importer_factory(store=store, schemas=schemas, adapters=adapters)(
            wiring.pack_source()
        )
        result = import_pack.import_pack(importer, pack)
    print(import_pack.format_result(result))
    return 0


@app.command(
    "rebuild",
    short_help="drop the learning-loop projections and replay the event log",
    help=(
        "Rebuild the learning-loop projections and v0.2 views from stored events (DATABASE_URL). "
        "The pack projection is rebuilt by 'import', not from the log."
    ),
)
def rebuild_cmd() -> int:
    with wiring.event_store() as store, wiring.event_store_v2() as store_v2:
        replayed = rebuild.rebuild(store, store_v2)
    print(rebuild.format_result(replayed))
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
