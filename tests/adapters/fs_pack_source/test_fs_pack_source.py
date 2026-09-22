"""Tests for the local filesystem PackSource adapter (ADR-0001, ADR-0015, ADR-0017).

Behaviour is expected to match ``InMemoryPackSource`` (``harness/testing/fakes.py``),
the Port's reference semantics, plus filesystem-only concerns: hidden files,
directory exclusion, and symlink rejection (the Port never follows a symlink,
see ``harness/core/ports/pack_source.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.adapters.fs_pack_source import FilesystemPackSource
from harness.core.ports import (
    PackFileNotFoundError,
    PackLocationNotFoundError,
    PackPathError,
    PackSource,
)


def _write(root: Path, relative: str, content: bytes = b"") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_satisfies_pack_source_protocol() -> None:
    source: PackSource = FilesystemPackSource()
    assert source is not None


def test_list_files_is_sorted_pack_relative_posix_and_excludes_directories(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "manifest.yaml", b"m")
    _write(tmp_path, "skills/b.yaml", b"b")
    _write(tmp_path, "skills/a.yaml", b"a")
    (tmp_path / "empty_dir").mkdir()

    source = FilesystemPackSource()
    assert source.list_files(str(tmp_path)) == [
        "manifest.yaml",
        "skills/a.yaml",
        "skills/b.yaml",
    ]


def test_list_files_includes_hidden_files(tmp_path: Path) -> None:
    _write(tmp_path, ".hidden", b"h")
    _write(tmp_path, "visible.yaml", b"v")

    source = FilesystemPackSource()
    assert source.list_files(str(tmp_path)) == [".hidden", "visible.yaml"]


def test_list_files_raises_for_missing_location(tmp_path: Path) -> None:
    source = FilesystemPackSource()
    with pytest.raises(PackLocationNotFoundError):
        source.list_files(str(tmp_path / "nowhere"))


def test_list_files_raises_for_location_that_is_a_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "not_a_dir", b"x")
    source = FilesystemPackSource()
    with pytest.raises(PackLocationNotFoundError):
        source.list_files(str(file_path))


def test_list_files_raises_on_symlinked_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_file.yaml"
    outside.write_bytes(b"secret")
    (tmp_path / "link.yaml").symlink_to(outside)

    source = FilesystemPackSource()
    with pytest.raises(PackPathError):
        source.list_files(str(tmp_path))


def test_list_files_raises_on_symlinked_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside_dir"
    outside.mkdir()
    (outside / "f.yaml").write_bytes(b"f")
    (tmp_path / "link_dir").symlink_to(outside, target_is_directory=True)

    source = FilesystemPackSource()
    with pytest.raises(PackPathError):
        source.list_files(str(tmp_path))


def test_read_bytes_returns_exact_content(tmp_path: Path) -> None:
    _write(tmp_path, "skills/a.yaml", b"hello")
    source = FilesystemPackSource()
    assert source.read_bytes(str(tmp_path), "skills/a.yaml") == b"hello"


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "a//b", "./a", "a\\b", "a/.", "../escape"])
def test_read_bytes_rejects_malformed_or_traversal_paths(tmp_path: Path, path: str) -> None:
    source = FilesystemPackSource()
    with pytest.raises(PackPathError):
        source.read_bytes(str(tmp_path), path)


def test_read_bytes_raises_for_missing_file(tmp_path: Path) -> None:
    source = FilesystemPackSource()
    with pytest.raises(PackFileNotFoundError):
        source.read_bytes(str(tmp_path), "missing.yaml")


def test_read_bytes_raises_for_missing_location(tmp_path: Path) -> None:
    source = FilesystemPackSource()
    with pytest.raises(PackLocationNotFoundError):
        source.read_bytes(str(tmp_path / "nowhere"), "manifest.yaml")


def test_read_bytes_raises_when_path_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    source = FilesystemPackSource()
    with pytest.raises(PackFileNotFoundError):
        source.read_bytes(str(tmp_path), "skills")


def test_read_bytes_rejects_symlinked_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_secret.yaml"
    outside.write_bytes(b"secret")
    (tmp_path / "link.yaml").symlink_to(outside)

    source = FilesystemPackSource()
    with pytest.raises(PackPathError):
        source.read_bytes(str(tmp_path), "link.yaml")


def test_read_bytes_rejects_path_through_symlinked_directory_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside_dir"
    outside.mkdir()
    (outside / "secret.yaml").write_bytes(b"secret")
    (tmp_path / "link_dir").symlink_to(outside, target_is_directory=True)

    source = FilesystemPackSource()
    with pytest.raises(PackPathError):
        source.read_bytes(str(tmp_path), "link_dir/secret.yaml")
