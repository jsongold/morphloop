"""Local filesystem PackSource adapter (ADR-0001, ADR-0009, ADR-0015, ADR-0017).

Implements ``harness.core.ports.pack_source.PackSource`` by reading directly
from a directory on disk. ``location`` is a filesystem path to a pack
directory (``contents/<pack-id>/``); this adapter never imports or otherwise
reads ``contents/`` itself, it only ever receives ``location`` from wiring
(the Importer) and reads bytes under it.

Symlinks: this adapter never follows a symlink, anywhere. A symlink -- a
file, a directory, or any path component on the way to one -- raises
:class:`~harness.core.ports.pack_source.PackPathError` rather than being
silently resolved or skipped. This is the Port's documented contract
(see ``harness/core/ports/pack_source.py``): the safest rule is "do not
follow symlinks at all", so a pack can never read, or even have listed,
something outside its location via a symlink.

Hidden files (dotfiles) are not filtered: :meth:`FilesystemPackSource.list_files`
returns every regular file under ``location``. Deciding which files belong to
the pack is the Importer's job against ``manifest.files``, not this adapter's.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from harness.core.ports.pack_source import (
    PackFileNotFoundError,
    PackLocationNotFoundError,
    PackPathError,
    check_pack_path,
)


class FilesystemPackSource:
    """:class:`~harness.core.ports.PackSource` reading pack files from local disk."""

    def list_files(self, location: str) -> Sequence[str]:
        root = _resolve_root(location)
        paths: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            # os.walk (followlinks=False) never descends into a symlinked
            # directory, but it would silently omit it unless we raise here.
            for dirname in dirnames:
                entry = current / dirname
                if entry.is_symlink():
                    raise PackPathError(f"symlink in pack: {entry.relative_to(root).as_posix()}")
            for filename in filenames:
                entry = current / filename
                if entry.is_symlink():
                    raise PackPathError(f"symlink in pack: {entry.relative_to(root).as_posix()}")
                paths.append(entry.relative_to(root).as_posix())
        return sorted(paths)

    def read_bytes(self, location: str, path: str) -> bytes:
        check_pack_path(path)
        root = _resolve_root(location)
        target = root / path
        if _has_symlink_component(root, target):
            raise PackPathError(f"symlink in pack: {path}")
        try:
            return target.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            raise PackFileNotFoundError(f"{location}:{path}") from None


def _resolve_root(location: str) -> Path:
    root = Path(location)
    if not root.is_dir():
        raise PackLocationNotFoundError(location)
    return root


def _has_symlink_component(root: Path, target: Path) -> bool:
    """True if any component of ``target`` relative to ``root`` is a symlink.

    A missing component is not a symlink (``Path.is_symlink`` is False for a
    path that does not exist), so this only ever reports real symlinks; a
    genuinely missing file is left for the caller to report as not found.
    """
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False
