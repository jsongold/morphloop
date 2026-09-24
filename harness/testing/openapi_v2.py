"""Loader that merges `contracts/openapi/v0.2/` into one OpenAPI document.

`contracts/openapi/v0.2/root.yaml` keeps `paths: {}` on disk forever: each
resource owns a URL -> Path Item map in its own `paths/<resource>.yaml` file
(ADR-0018, issue #44). Two resource PRs adding different URLs then touch
different files and never conflict on adjacent lines of a shared `paths:`
block. `load_merged_openapi_v2_spec` builds the full document in memory by
splicing every `paths/*.yaml` into root's `paths`, raising `DuplicatePathError`
if the same URL is declared twice.

A `$ref` inside a resource's paths file is written relative to that file's
own directory (e.g. `../components/common.yaml#/Stub`). Once spliced into the
merged document, only `root.yaml`'s own base URI applies during validation,
so such refs are rewritten here to absolute `file://` URIs (still relative to
their *source* file) before merging, keeping them resolvable regardless of
where they end up in the merged tree.

Used by `tests/contracts/test_openapi_v2_contract.py`; a future harness API
or docs surface that serves the v0.2 OpenAPI document can reuse it too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml

from harness.testing.contracts import CONTRACTS_DIR

V2_DIR = CONTRACTS_DIR / "openapi" / "v0.2"


class DuplicatePathError(ValueError):
    """Raised when two `paths/*.yaml` files declare the same URL."""


def _rewrite_relative_refs(node: Any, base_uri: str) -> Any:
    """Resolve every relative `$ref` string in `node` against `base_uri`."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and not value.startswith(("#", "http://", "https://", "file://"))
            ):
                out[key] = urljoin(base_uri, value)
            else:
                out[key] = _rewrite_relative_refs(value, base_uri)
        return out
    if isinstance(node, list):
        return [_rewrite_relative_refs(item, base_uri) for item in node]
    return node


def load_merged_openapi_v2_spec(v2_dir: Path = V2_DIR) -> dict[str, Any]:
    """Return `root.yaml` with `paths` filled in from every `paths/*.yaml`."""
    root_path = v2_dir / "root.yaml"
    spec: dict[str, Any] = yaml.safe_load(root_path.read_text(encoding="utf-8"))

    merged_paths: dict[str, Any] = {}
    for path_file in sorted((v2_dir / "paths").glob("*.yaml")):
        resource_paths: dict[str, Any] = yaml.safe_load(path_file.read_text(encoding="utf-8")) or {}
        base_uri = path_file.resolve().as_uri()
        for url, item in resource_paths.items():
            if url in merged_paths:
                raise DuplicatePathError(
                    f"{url!r} is declared in more than one paths/*.yaml file "
                    f"(most recently {path_file.name})"
                )
            merged_paths[url] = _rewrite_relative_refs(item, base_uri)

    spec["paths"] = merged_paths
    return spec
