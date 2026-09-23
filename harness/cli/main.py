"""The ``morphloop`` command line: ``generate``, ``import``, ``rebuild`` (ADR-0017).

Run it as a module (``pyproject.toml`` declares no console script)::

    uv run python -m harness.cli generate contents/software-engineering \\
        --template diagnose-dns-resolver-misconfiguration
    uv run python -m harness.cli import contents/software-engineering
    uv run python -m harness.cli rebuild

Exit codes: ``0`` success, ``1`` a reported failure
(:class:`~harness.cli.errors.CommandError`), ``2`` a usage error from argparse.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from harness.cli import import_pack, rebuild, wiring
from harness.cli.errors import CommandError
from harness.cli.generate import ActivityGenerator, GenerateRequest
from harness.cli.pack_files import FilesystemPackWriter
from harness.core.loop import utc_now

PROG = "python -m harness.cli"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Author, import and maintain morphloop content.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser(
        "generate",
        help="run the authoring generation path for one Template and write the result",
        description=(
            "Generate one activity from a Template with the pack's Generator, validate it "
            "(schema, adapter references, and a real lab in which the checks must fail "
            "before and pass after the reference solution), then write the finalized "
            "Definition files and the generation record into the pack."
        ),
    )
    generate.add_argument("pack", help="path to the pack directory, e.g. contents/<pack-id>")
    generate.add_argument("--template", required=True, help="id of the activity_template to run")
    generate.add_argument(
        "--activity-id",
        default=None,
        help="id for the generated activity (default: the next free gen-<template>-NNN)",
    )
    generate.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="candidates to try (default: the pack's max_regenerations + 1)",
    )

    importing = commands.add_parser(
        "import",
        help="validate a pack and write its projection to the database",
        description=(
            "Parse, validate and hash the pack at PACK and write the pack projection "
            "through the event store (DATABASE_URL). Importing unchanged content again "
            "writes nothing."
        ),
    )
    importing.add_argument("pack", help="path to the pack directory, e.g. contents/<pack-id>")

    commands.add_parser(
        "rebuild",
        help="drop the learning-loop projections and replay the event log",
        description=(
            "Rebuild the learning-loop projections from the stored events (DATABASE_URL). "
            "The pack projection is rebuilt by 'import', not from the log."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            return _generate(args)
        if args.command == "import":
            return _import(args)
        return _rebuild()
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _generate(args: argparse.Namespace) -> int:
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
            location=args.pack,
            template_id=args.template,
            activity_id=args.activity_id,
            max_attempts=args.max_attempts,
        )
    )
    print(f"activity      {result.activity_id}")
    print(f"attempts      {result.attempts}")
    for candidate in result.rejected:
        print(f"  rejected #{candidate.attempt} [{candidate.failed_step}] {candidate.reason}")
    for path in result.written:
        print(f"wrote         {path}")
    return 0


def _import(args: argparse.Namespace) -> int:
    schemas = wiring.contract_schemas()
    adapters = wiring.domain_adapters()
    with wiring.event_store() as store:
        importer = wiring.importer_factory(store=store, schemas=schemas, adapters=adapters)(
            wiring.pack_source()
        )
        result = import_pack.import_pack(importer, args.pack)
    print(import_pack.format_result(result))
    return 0


def _rebuild() -> int:
    with wiring.event_store() as store:
        replayed = rebuild.rebuild(store)
    print(rebuild.format_result(replayed))
    return 0
