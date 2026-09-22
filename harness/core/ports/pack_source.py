"""Pack source Port (ADR-0001, ADR-0015).

Where pack files come from. The Pack Importer lists and reads a pack's files
through this Port, then parses, validates and hashes them itself; the Port
does no parsing and knows nothing about the pack format. v0.1 has one adapter,
the local filesystem (``harness/adapters/fs_pack_source/``); Git / OCI / S3 are
later adapters.

``location`` is an opaque string handed in by wiring (v0.1: a local directory
path). The harness never imports ``contents/``; it only ever receives a
location and reads bytes from it.

Paths are pack-relative POSIX paths (``/`` separators, no leading ``/``, no
``.`` or ``..`` segments, no empty segments), the same form as
``manifest.files`` keys. :func:`check_pack_path` checks that form.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class PackSourceError(Exception):
    """Base class for pack source failures raised by adapters."""


class PackLocationNotFoundError(PackSourceError):
    """The pack location does not exist or cannot be opened."""


class PackFileNotFoundError(PackSourceError):
    """The requested file is not in the pack."""


class PackPathError(PackSourceError):
    """A path is malformed, escapes the pack, or is a symlink."""


def check_pack_path(path: str) -> None:
    """Raise :class:`PackPathError` unless ``path`` is a pack-relative POSIX path."""
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        raise PackPathError(f"not a pack-relative POSIX path: {path!r}")
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise PackPathError(f"not a pack-relative POSIX path: {path!r}")


class PackSource(Protocol):
    """Read-only access to the files of one pack location (synchronous)."""

    def list_files(self, location: str) -> Sequence[str]:
        """Return every regular file under ``location`` as pack-relative POSIX
        paths, sorted by code point so the Importer's hash is deterministic.

        Nothing is filtered out (hidden files included): which files belong to
        the pack is the Importer's decision against ``manifest.files``. A symlink
        anywhere in the pack raises :class:`PackPathError` rather than being
        followed or skipped, so a pack can never read outside its location.
        Raises :class:`PackLocationNotFoundError` for a missing location.
        """
        ...

    def read_bytes(self, location: str, path: str) -> bytes:
        """Return the exact bytes of ``path`` in ``location``.

        Raises :class:`PackPathError` if ``path`` fails :func:`check_pack_path`
        or is a symlink, :class:`PackFileNotFoundError` if it does not exist,
        and :class:`PackLocationNotFoundError` for a missing location.
        """
        ...
