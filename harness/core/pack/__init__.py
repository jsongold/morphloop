"""Pack import and the pack projection read API (ADR-0015).

- ``importer`` -- :class:`PackImporter` (``check`` / ``import_pack``).
- ``catalog`` -- :class:`PackCatalog`, the only runtime read path.
- ``model`` -- typed pack objects, projection names and keys, exposure rules.
- ``canonical_json`` -- RFC 8785 JCS, document hash and pack content hash.
- ``parsing`` -- JSON/YAML pack file parsing.
"""

from harness.core.pack.canonical_json import (
    canonicalize,
    document_hash,
    pack_content_hash,
    sha256_hash,
)
from harness.core.pack.catalog import PackCatalog, PackNotFoundError
from harness.core.pack.importer import (
    ImportProblem,
    ImportResult,
    PackImporter,
    PackImportError,
    PackProjectionConflictError,
    ParsedPack,
)
from harness.core.pack.model import (
    DEFINITION_PROJECTION,
    LEARNER_FACING_KINDS,
    LEARNER_VISIBLE_ACTIVITY_FIELDS,
    PACK_PROJECTION,
    SECRET_PROJECTION,
    Definition,
    LoadedPack,
    PackFileEntry,
    PackRef,
    Prompt,
    ReferenceSolution,
    learner_view_of_activity,
)
from harness.core.pack.parsing import PackParseError

__all__ = [
    "DEFINITION_PROJECTION",
    "LEARNER_FACING_KINDS",
    "LEARNER_VISIBLE_ACTIVITY_FIELDS",
    "PACK_PROJECTION",
    "SECRET_PROJECTION",
    "Definition",
    "ImportProblem",
    "ImportResult",
    "LoadedPack",
    "PackCatalog",
    "PackFileEntry",
    "PackImportError",
    "PackImporter",
    "PackNotFoundError",
    "PackParseError",
    "PackProjectionConflictError",
    "PackRef",
    "ParsedPack",
    "Prompt",
    "ReferenceSolution",
    "canonicalize",
    "document_hash",
    "learner_view_of_activity",
    "pack_content_hash",
    "sha256_hash",
]
