"""Tests for ``harness.cli.import_pack``: importing is idempotent and refusals write nothing."""

from __future__ import annotations

import json

import pytest
from cli_pack_fixture import (
    LOCATION,
    SCHEMAS,
    Files,
    adapters,
    load,
    pack_files,
    pack_source,
)

from harness.cli.errors import CommandError
from harness.cli.import_pack import format_result, import_pack
from harness.core.pack import (
    DEFINITION_PROJECTION,
    PACK_PROJECTION,
    SECRET_PROJECTION,
    PackImporter,
    pack_content_hash,
)
from harness.core.registry.builtin import v01_algorithm_registry
from harness.testing.fakes import InMemoryEventStore


def _importer(files: Files, store: InMemoryEventStore) -> PackImporter:
    return PackImporter(
        source=pack_source(files),
        store=store,
        schemas=SCHEMAS,
        adapters=adapters(),
        algorithms=v01_algorithm_registry(),
    )


def test_reports_the_pack_identity_and_that_it_was_created() -> None:
    files = pack_files()
    store = InMemoryEventStore()

    result = import_pack(_importer(files, store), LOCATION)

    assert result.created
    assert result.ref.pack_id == "software-engineering"
    assert result.ref.pack_version == "0.1.0"
    assert result.ref.content_hash == pack_content_hash(files.items())
    assert "status        imported" in format_result(result)


def test_importing_the_same_pack_again_changes_nothing() -> None:
    files = pack_files()
    store = InMemoryEventStore()
    first = import_pack(_importer(files, store), LOCATION)
    with store.transaction() as tx:
        before = {
            name: dict(tx.list_projection(name))
            for name in (PACK_PROJECTION, DEFINITION_PROJECTION, SECRET_PROJECTION)
        }

    second = import_pack(_importer(files, store), LOCATION)

    assert not second.created
    assert second.ref == first.ref
    with store.transaction() as tx:
        after = {
            name: dict(tx.list_projection(name))
            for name in (PACK_PROJECTION, DEFINITION_PROJECTION, SECRET_PROJECTION)
        }
    assert after == before
    assert "already imported" in format_result(second)


def test_a_refused_pack_is_reported_and_writes_nothing() -> None:
    files = pack_files()
    manifest = load(files, "manifest.json")
    del manifest["files"]["evaluators/dns-diagnosis-v1.json"]
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    store = InMemoryEventStore()

    with pytest.raises(CommandError) as excinfo:
        import_pack(_importer(files, store), LOCATION)

    assert "nothing was written" in str(excinfo.value)
    with store.transaction() as tx:
        for name in (PACK_PROJECTION, DEFINITION_PROJECTION, SECRET_PROJECTION):
            assert tx.list_projection(name) == []


def test_a_missing_pack_location_is_reported() -> None:
    store = InMemoryEventStore()

    with pytest.raises(CommandError) as excinfo:
        import_pack(_importer(pack_files(), store), "packs/absent")

    assert "cannot read the pack" in str(excinfo.value)
