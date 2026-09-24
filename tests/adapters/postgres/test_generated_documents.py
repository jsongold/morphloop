"""GeneratedDocumentStore contract suite, run against Postgres and the in-memory fake.

The Postgres table is shared across tests, so each test uses a fresh resource name.
"""

from __future__ import annotations

import threading
import uuid

import pytest

from harness.adapters.postgres.generated_documents import PostgresGeneratedDocumentStore
from harness.core.ports.generated_documents import (
    GeneratedDocument,
    GeneratedDocumentExistsError,
    GeneratedDocumentStore,
)
from harness.testing.generated_documents import InMemoryGeneratedDocumentStore


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> GeneratedDocumentStore:
    if request.param == "memory":
        return InMemoryGeneratedDocumentStore()
    return PostgresGeneratedDocumentStore(request.getfixturevalue("pg_engine"))


@pytest.fixture
def resource() -> str:
    return f"res_{uuid.uuid4().hex}"


def _doc(resource: str, id: str, *labels: str) -> GeneratedDocument:
    return GeneratedDocument(
        resource=resource,
        id=id,
        body={"text": f"doc {id}", "n": [1, 2.5, None, True]},
        labels=labels,
        provenance={"model": "m", "prompt": {"id": "p", "version": 1}},
    )


def test_add_then_get_roundtrips(store: GeneratedDocumentStore, resource: str) -> None:
    doc = _doc(resource, "a", "x", "y")
    store.add(doc)
    assert store.get(resource, "a") == doc
    assert store.get(resource, "missing") is None
    assert store.get(f"{resource}_other", "a") is None


def test_duplicate_add_raises_and_keeps_first(store: GeneratedDocumentStore, resource: str) -> None:
    first = _doc(resource, "a")
    store.add(first)
    with pytest.raises(GeneratedDocumentExistsError):
        store.add(_doc(resource, "a", "changed"))
    assert store.get(resource, "a") == first


def test_same_id_in_other_resource_is_independent(
    store: GeneratedDocumentStore, resource: str
) -> None:
    store.add(_doc(resource, "a"))
    store.add(_doc(f"{resource}_other", "a"))


def test_list_by_resource_and_label_sorted_by_id(
    store: GeneratedDocumentStore, resource: str
) -> None:
    for id, labels in (("b", ("x",)), ("a", ("x", "y")), ("c", ())):
        store.add(_doc(resource, id, *labels))
    store.add(_doc(f"{resource}_other", "z", "x"))
    assert [d.id for d in store.list(resource)] == ["a", "b", "c"]
    assert [d.id for d in store.list(resource, label="x")] == ["a", "b"]
    assert [d.id for d in store.list(resource, label="y")] == ["a"]
    assert store.list(resource, label="none") == []


def test_concurrent_adds_store_one(store: GeneratedDocumentStore, resource: str) -> None:
    errors: list[Exception] = []

    def add() -> None:
        try:
            store.add(_doc(resource, "a"))
        except GeneratedDocumentExistsError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=add) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 7
    assert len(store.list(resource)) == 1
