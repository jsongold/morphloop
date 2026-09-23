"""Reading a pack with pending files, and writing files into a pack (ADR-0014).

The pack source Port is read-only: it is how the harness *reads* a pack
(``harness/core/ports/pack_source.py``). ``authoring`` generation is the one
place that adds files to a pack, and it runs from this CLI, so the write side
lives here rather than in core or in an adapter.

Two pieces:

:class:`OverlayPackSource`
    a :class:`~harness.core.ports.PackSource` showing a pack as it *would* be
    with the generated files in it. The generator runs the whole
    :class:`~harness.core.pack.PackImporter` over the overlay before anything
    touches the disk, so a pack that would not import is never written
    (AC-J2: a candidate that fails validation is never stored).
:class:`PackWriter` / :class:`FilesystemPackWriter`
    the write itself. A finalized Definition is immutable (ADR-0014), so
    writing over an existing file is refused unless the caller explicitly asks
    to replace it, which only the manifest file index ever does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from harness.core.ports import PackSource, PlainJson
from harness.core.ports.pack_source import (
    PackLocationNotFoundError,
    PackPathError,
    check_pack_path,
)

INDENT = 2


def dump_document(document: PlainJson) -> bytes:
    """Serialize a pack document: UTF-8 JSON, two-space indent, trailing newline.

    Formatting is irrelevant to identity -- a document hash is taken over the
    JCS form of the parsed document (``harness.core.pack.canonical_json``) --
    so this only has to be stable and reviewable in git.
    """
    return (json.dumps(document, indent=INDENT, ensure_ascii=False) + "\n").encode("utf-8")


class OverlayPackSource:
    """``base`` with ``files`` laid over one location (pending, not yet written)."""

    def __init__(self, base: PackSource, *, location: str, files: Mapping[str, bytes]) -> None:
        for path in files:
            check_pack_path(path)
        self._base = base
        self._location = location
        self._files = dict(files)

    def list_files(self, location: str) -> Sequence[str]:
        existing = self._base.list_files(location)
        if location != self._location:
            return existing
        return sorted(set(existing) | set(self._files))

    def read_bytes(self, location: str, path: str) -> bytes:
        check_pack_path(path)
        if location == self._location and path in self._files:
            return self._files[path]
        return self._base.read_bytes(location, path)


class PackWriter(Protocol):
    """Writes one file into a pack location."""

    def write_bytes(self, location: str, path: str, data: bytes, *, replace: bool = False) -> None:
        """Write ``data`` at pack-relative ``path``.

        Raises :class:`~harness.core.ports.pack_source.PackPathError` unless
        ``path`` passes :func:`~harness.core.ports.pack_source.check_pack_path`
        and nothing on the way to it is a symlink, and
        :class:`FileExistsError` when the file exists and ``replace`` is false.
        """
        ...


class FilesystemPackWriter:
    """:class:`PackWriter` writing into a pack directory on the local disk.

    The counterpart of
    :class:`~harness.adapters.fs_pack_source.FilesystemPackSource`, and it
    follows the same rule: no symlink is ever followed, so a write can never
    land outside the pack directory.
    """

    def write_bytes(self, location: str, path: str, data: bytes, *, replace: bool = False) -> None:
        check_pack_path(path)
        root = Path(location)
        if not root.is_dir():
            raise PackLocationNotFoundError(location)
        if root.is_symlink():
            raise PackPathError(f"pack location is a symlink: {location}")
        target = root
        segments = path.split("/")
        for segment in segments:
            target = target / segment
            if target.is_symlink():
                raise PackPathError(f"symlink in pack: {path}")
        if target.exists() and not replace:
            raise FileExistsError(f"{location}/{path} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _conforms() -> None:  # pragma: no cover - evaluated by mypy only
    from harness.adapters.fs_pack_source import FilesystemPackSource

    _source: PackSource = OverlayPackSource(FilesystemPackSource(), location="", files={})
    _writer: PackWriter = FilesystemPackWriter()
