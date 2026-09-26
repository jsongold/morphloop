"""View base: registration by definition, in-transaction dispatch, rebuild, reads."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from harness.core import view as view_module
from harness.core.ports.events_v2 import EventV2, StoredEventV2, ViewDocumentStore
from harness.core.view import View, dispatch, registered_views
from harness.testing.fakes_v2 import (
    PROBE_EVENT_TYPE,
    InMemoryEventStoreV2,
    contract_schemas_with_probe,
)


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    saved = dict(view_module._REGISTRY)
    yield
    view_module._REGISTRY.clear()
    view_module._REGISTRY.update(saved)


def _event(n: int, note: str) -> EventV2:
    return EventV2(
        id=f"0190f5a2-7c3e-7d4b-8a1f-{n:012d}",
        type=PROBE_EVENT_TYPE,
        actor="learner",
        user_id="usr_01",
        payload={"note": note},
    )


def _notes_view() -> type[View]:
    class Notes(View):
        name = "test_notes"
        handles: ClassVar[frozenset[str]] = frozenset({PROBE_EVENT_TYPE})

        @classmethod
        def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
            doc = cls.get(tx, event.user_id) or {"notes": []}
            notes = [*doc["notes"], event.payload["note"]]  # type: ignore[misc]
            tx.put_view(cls.name, event.user_id, {"notes": notes})

    return Notes


def test_defining_a_subclass_registers_it() -> None:
    notes = _notes_view()
    assert registered_views()["test_notes"] is notes


def test_dispatch_updates_views_in_the_appending_transaction(tmp_path: Path) -> None:
    notes = _notes_view()
    store = InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path))
    with store.transaction() as tx:
        dispatch(tx.append(_event(1, "a")).event, tx)
        dispatch(tx.append(_event(2, "b")).event, tx)
    with store.transaction() as tx:
        assert notes.get(tx, "usr_01") == {"notes": ["a", "b"]}
        assert notes.list(tx, key_prefix="usr") == [("usr_01", {"notes": ["a", "b"]})]

    with pytest.raises(RuntimeError), store.transaction() as tx:
        dispatch(tx.append(_event(3, "c")).event, tx)
        raise RuntimeError("roll back")
    with store.transaction() as tx:
        assert notes.get(tx, "usr_01") == {"notes": ["a", "b"]}


def test_list_pages_through_to_the_store(tmp_path: Path) -> None:
    class Items(View):
        name = "test_items"
        handles: ClassVar[frozenset[str]] = frozenset({PROBE_EVENT_TYPE})

        @classmethod
        def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
            tx.put_view(cls.name, event.payload["note"], {"note": event.payload["note"]})

    store = InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path))
    with store.transaction() as tx:
        for n, note in enumerate(["b", "a", "c"], start=1):
            dispatch(tx.append(_event(n, note)).event, tx)

    with store.transaction() as tx:
        # Without a limit, existing (pre-#173) callers still get the whole list.
        assert Items.list(tx) == [("a", {"note": "a"}), ("b", {"note": "b"}), ("c", {"note": "c"})]

        page, cursor = Items.list(tx, limit=2, after=None)
        assert [k for k, _ in page] == ["a", "b"] and cursor == "b"
        page, cursor = Items.list(tx, limit=2, after=cursor)
        assert [k for k, _ in page] == ["c"] and cursor is None


def test_dispatch_skips_views_that_do_not_handle_the_type(tmp_path: Path) -> None:
    class Other(View):
        name = "test_other"
        handles: ClassVar[frozenset[str]] = frozenset({"other.created"})

        @classmethod
        def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
            raise AssertionError("not handled")

    store = InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path))
    with store.transaction() as tx:
        dispatch(tx.append(_event(1, "a")).event, tx)


def test_rebuild_clears_and_replays(tmp_path: Path) -> None:
    notes = _notes_view()
    store = InMemoryEventStoreV2(contract_schemas_with_probe(tmp_path))
    with store.transaction() as tx:
        for n, note in enumerate("xyz", start=1):
            dispatch(tx.append(_event(n, note)).event, tx)
        tx.put_view(notes.name, "stale", {"notes": []})
    with store.transaction() as tx:
        notes.rebuild(store.read(), tx)
        assert notes.list(tx) == [("usr_01", {"notes": ["x", "y", "z"]})]


def test_subclass_must_declare_name_and_handles() -> None:
    with pytest.raises(TypeError, match="name and non-empty handles"):

        class _Missing(View):
            name = "test_missing"


def test_duplicate_view_name_is_rejected() -> None:
    _notes_view()

    with pytest.raises(TypeError, match="already registered"):

        class _Clash(View):
            name = "test_notes"
            handles: ClassVar[frozenset[str]] = frozenset({PROBE_EVENT_TYPE})
