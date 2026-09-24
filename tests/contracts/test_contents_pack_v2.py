"""Contract tests for the real v2 pack under contents/v2/software-engineering/.

Validates every file listed in the manifest against its pack v2 schema (as
tests/contracts/test_pack_v2_contracts.py does for the fixture pack) and checks the
cross-file rules the Importer enforces that need no adapter registry: labels are
'sys:holdout', 'topic:<existing topic>' or in the vocabulary; a choice item's
expected answer is one of its choices; 'artifact_ref' and '::artifact{...}' refs
name an artifact spec (of that type).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from harness.testing.contracts import CONTRACTS_DIR, validate

V2 = "schemas/pack/v2/"
PACK_DIR = CONTRACTS_DIR.parent / "contents" / "v2" / "software-engineering"
LIST_SCHEMA = {
    "topics": "topic.json",
    "textbooks": "textbook-doc.json",
    "drills": "drill-item.json",
    "artifacts": "artifact-spec.json",
}
DIRECTIVE = re.compile(r"::artifact\{type=([a-z0-9._-]+) ref=([a-z0-9._-]+)\}")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = _load(PACK_DIR / "manifest.json")
    return manifest


def _listed() -> list[tuple[str, str]]:
    manifest = _manifest()
    pairs = [(manifest["labels"], "labels.json")]
    pairs += [(p, schema) for key, schema in LIST_SCHEMA.items() for p in manifest[key]]
    return pairs


def _docs(kind: str) -> list[dict[str, Any]]:
    return [_load(PACK_DIR / p) for p in _manifest()[kind]]


def _topic_ids(topic: dict[str, Any]) -> Iterator[str]:
    yield topic["id"]
    for child in topic.get("topics", []):
        yield from _topic_ids(child)


def _labelled() -> Iterator[tuple[str, list[str]]]:
    for kind in ("textbooks", "drills", "artifacts"):
        for doc in _docs(kind):
            yield f"{kind}/{doc['id']}", doc["labels"]
            for block in doc.get("blocks", []):
                yield f"{kind}/{doc['id']}#{block['id']}", block["labels"]


def test_manifest_is_valid() -> None:
    validate(_manifest(), V2 + "manifest.json")


@pytest.mark.parametrize(("rel", "schema"), _listed(), ids=lambda v: str(v))
def test_listed_file_is_valid(rel: str, schema: str) -> None:
    validate(_load(PACK_DIR / rel), V2 + schema)


def test_every_pack_file_is_listed() -> None:
    on_disk = {
        p.relative_to(PACK_DIR).as_posix()
        for p in PACK_DIR.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert on_disk == {rel for rel, _ in _listed()}


def test_topic_ids_are_unique() -> None:
    ids = [i for topic in _docs("topics") for i in _topic_ids(topic)]
    assert len(ids) == len(set(ids))


def _topic_docs(topic: dict[str, Any]) -> Iterator[str]:
    yield from topic.get("docs", [])
    for child in topic.get("topics", []):
        yield from _topic_docs(child)


def test_topic_docs_cover_every_textbook_doc_exactly_once() -> None:
    textbook_ids = {doc["id"] for doc in _docs("textbooks")}
    listed = [doc_id for topic in _docs("topics") for doc_id in _topic_docs(topic)]
    unknown = set(listed) - textbook_ids
    assert not unknown, f"topic.docs references unknown textbook doc(s): {sorted(unknown)}"
    assert len(listed) == len(set(listed)), "a textbook doc is listed under more than one topic"
    missing = textbook_ids - set(listed)
    assert not missing, f"textbook doc(s) not listed under any topic: {sorted(missing)}"


def test_labels_are_known() -> None:
    vocabulary = set(_load(PACK_DIR / _manifest()["labels"])["labels"])
    topics = {f"topic:{i}" for topic in _docs("topics") for i in _topic_ids(topic)}
    known = vocabulary | topics | {"sys:holdout"}
    unknown = [(w, label) for w, labels in _labelled() for label in labels if label not in known]
    assert not unknown


def test_choice_expected_is_a_choice() -> None:
    for item in _docs("drills"):
        if item["answer_mode"] == "choice":
            assert item["expected"] in item["choices"], item["id"]


def test_artifact_refs_resolve() -> None:
    types = {a["id"]: a["type"] for a in _docs("artifacts")}
    for item in _docs("drills"):
        if "artifact_ref" in item:
            assert item["artifact_ref"] in types, item["id"]
    directives = [
        (doc["id"], m)
        for doc in _docs("textbooks")
        for b in doc["blocks"]
        for m in DIRECTIVE.findall(b["body"])
    ]
    assert directives, "no text embeds an artifact"
    for doc_id, (kind, ref) in directives:
        assert types.get(ref) == kind, f"{doc_id}: ::artifact{{type={kind} ref={ref}}}"
