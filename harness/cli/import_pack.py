"""``import``: load a pack from a path into the pack projection (ADR-0015).

The files stay the source of truth (ADR-0007); the Importer parses, validates,
hashes and writes the projection the runtime reads. Projections are keyed by
pack id + version + content hash and are never overwritten, so importing the
same pack twice is a no-op and importing changed content under the same
version label is refused rather than silently replacing what attempts already
reference.
"""

from __future__ import annotations

from harness.cli.errors import CommandError
from harness.core.pack import (
    ImportResult,
    PackImporter,
    PackImportError,
    PackProjectionConflictError,
)
from harness.core.ports.pack_source import PackSourceError


def import_pack(importer: PackImporter, location: str) -> ImportResult:
    """Import the pack at ``location``; raises :class:`CommandError` if refused."""
    try:
        return importer.import_pack(location)
    except PackImportError as exc:
        raise CommandError(
            f"the pack at {location} was refused; nothing was written:\n"
            + "\n".join(f"  {problem}" for problem in exc.problems)
        ) from exc
    except PackProjectionConflictError as exc:
        raise CommandError(str(exc)) from exc
    except PackSourceError as exc:
        raise CommandError(f"cannot read the pack at {location}: {exc}") from exc


def format_result(result: ImportResult) -> str:
    state = "imported" if result.created else "already imported (unchanged)"
    return (
        f"pack_id       {result.ref.pack_id}\n"
        f"pack_version  {result.ref.pack_version}\n"
        f"content_hash  {result.ref.content_hash}\n"
        f"status        {state}"
    )
