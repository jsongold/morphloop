"""Pack v2 Importer (#53): the fixture pack imports; bad packs list every problem."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES, LabStub

from harness.core import artifact as artifact_module
from harness.core.artifact import Artifact
from harness.core.pack.v2 import PackV2, PackV2ImportError, import_pack_v2, validators
from harness.core.ports.json_types import JsonObject

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    dest = tmp_path / "pack"
    shutil.copytree(PACK, dest)
    return dest


def _edit(path: Path, edit: object) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)  # type: ignore[operator]
    path.write_text(json.dumps(doc), encoding="utf-8")


def _import(path: Path) -> PackV2:
    return import_pack_v2(path, artifact_types=PACK_ARTIFACT_TYPES)


def _problems(path: Path) -> str:
    with pytest.raises(PackV2ImportError) as info:
        _import(path)
    return "\n".join(info.value.problems)


def test_pack_imports() -> None:
    pack = _import(PACK)
    assert pack.pack_id == "software-engineering"
    assert pack.pack_hash.startswith("sha256:")
    assert "network.dns.resolution" in pack.topic_ids
    assert set(pack.documents) == {
        "labels",
        "topics",
        "textbooks",
        "drills",
        "artifacts",
        "llm_roles",
    }
    assert set(pack.llm_roles) == {"assistant"}
    assert pack.llm_roles["assistant"].prompt_text.startswith("# assistant")
    with pytest.raises(TypeError):
        pack.topics[0]["id"] = "x"  # type: ignore[index]


def test_hash_is_stable_and_content_bound(pack: Path) -> None:
    first = _import(PACK).pack_hash
    assert _import(PACK).pack_hash == first == _import(pack).pack_hash
    _edit(pack / "topics" / "network.json", lambda d: d.update(title="Networks"))
    assert _import(pack).pack_hash != first


def test_unlisted_file_is_rejected(pack: Path) -> None:
    (pack / "drills" / "stray.json").write_text("{}", encoding="utf-8")
    assert "drills/stray.json: file is not listed in the manifest" in _problems(pack)


def test_llm_role_missing_prompt_file_is_rejected(pack: Path) -> None:
    _edit(pack / "llm" / "assistant.json", lambda d: d.update(prompt="llm/missing.md"))
    problems = _problems(pack)
    assert "llm/assistant.json: prompt file 'llm/missing.md' does not exist" in problems
    assert "llm/assistant.md: file is not listed in the manifest" in problems


def test_duplicate_llm_role_name_is_rejected(pack: Path) -> None:
    shutil.copy(pack / "llm" / "assistant.json", pack / "llm" / "second.json")
    _edit(pack / "manifest.json", lambda d: d["llm_roles"].append("llm/second.json"))
    assert "[llm_roles] llm role 'assistant' is declared 2 times" in _problems(pack)


def test_duplicate_artifact_spec_id_is_rejected(pack: Path) -> None:
    shutil.copy(pack / "artifacts" / "dns-resolution-flow.json", pack / "artifacts" / "second.json")
    _edit(pack / "manifest.json", lambda d: d["artifacts"].append("artifacts/second.json"))
    assert "[artifact] artifact id 'dns-resolution-flow' appears 2 times" in _problems(pack)


def test_schema_error_names_the_file(pack: Path) -> None:
    _edit(pack / "topics" / "network.json", lambda d: d.pop("title"))
    assert "topics/network.json: $: 'title' is a required property" in _problems(pack)


def test_bad_label_and_duplicate_topic_are_reported_together(pack: Path) -> None:
    drill = pack / "drills" / "dns-record-choice.json"
    _edit(drill, lambda d: d["labels"].extend(["nope:unknown", "topic:missing"]))
    _edit(
        pack / "topics" / "network.json",
        lambda d: d["topics"].append({"id": "network.dns", "title": "Again"}),
    )
    problems = _problems(pack)
    where = "[labels] drills/dns-record-choice.json"
    assert f"{where}: label 'nope:unknown' not in the pack vocabulary" in problems
    assert "label 'topic:missing' topic id not in the topic tree" in problems
    assert "[topics] topic id 'network.dns' appears 2 times" in problems


def test_topic_docs_must_cover_textbook_docs_exactly_once(pack: Path) -> None:
    def edit(topic: dict[str, Any]) -> None:
        dns = topic["topics"][0]
        dns["docs"] = ["no-such-doc", *topic_docs(topic)[:1]]

    def topic_docs(topic: dict[str, Any]) -> list[str]:
        return [
            *topic.get("docs", []),
            *(d for t in topic.get("topics", []) for d in topic_docs(t)),
        ]

    _edit(pack / "topics" / "network.json", edit)
    problems = _problems(pack)
    assert "[topics] topic docs: 'no-such-doc' is not a textbook doc id" in problems
    assert "is listed under 2 topics" in problems


def test_new_validator_module_is_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "zz_always_fails.py").write_text(
        "def validate(pack):\n    return [f'{pack.pack_id} rejected']\n", encoding="utf-8"
    )
    monkeypatch.setattr(validators, "__path__", [*validators.__path__, str(plugin_dir)])
    name = f"{validators.__name__}.zz_always_fails"
    try:
        assert "[zz_always_fails] software-engineering rejected" in _problems(PACK)
    finally:
        sys.modules.pop(name, None)


LAB = "artifacts/dns-broken-resolver-lab.json"


def test_artifact_types_are_what_the_caller_passes(pack: Path) -> None:
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=[LabStub])
    problems = "\n".join(info.value.problems)
    assert (
        "artifacts/dns-resolution-flow.json: artifact type 'diagram' is not registered (lab)"
        in (problems)
    )
    assert LAB not in problems


def test_artifact_spec_is_validated_by_its_type_schema(pack: Path) -> None:
    _edit(pack / LAB, lambda d: d["spec"].pop("allowed_checks"))
    assert f"{LAB}: $.spec: 'allowed_checks' is a required property" in _problems(pack)
    _edit(pack / LAB, lambda d: d["spec"].update(allowed_checks=["dns.exit"], idle_seconds=0))
    assert f"{LAB}: $.spec.idle_seconds: 0 is less than the minimum of 1" in _problems(pack)


def test_artifact_spec_is_validated_by_its_type_validator(pack: Path) -> None:
    _edit(pack / LAB, lambda d: d["spec"].update(allowed_fixtures=["dns.other"]))
    problems = _problems(pack)
    assert (
        f"{LAB}: environment fixture 'dns.broken-resolver' is not in allowed_fixtures" in problems
    )


def test_validator_runs_only_on_a_schema_valid_spec_and_sees_the_pack(
    pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.delitem(artifact_module._REGISTRY, "diagram")  # let Picky take the name

    class Picky(Artifact):
        type: ClassVar[str] = "diagram"
        spec_schema: ClassVar[JsonObject] = {"type": "object", "required": ["actors"]}

        @classmethod
        def validate_spec(cls, spec: JsonObject, pack: PackV2) -> Iterable[str]:
            seen.append(pack.pack_id)
            return [f"{len(spec['actors'])} actors rejected"]

    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=[LabStub, Picky])
    assert info.value.problems == ("artifacts/dns-resolution-flow.json: 3 actors rejected",)
    assert seen == ["software-engineering"]
    _edit(pack / "artifacts" / "dns-resolution-flow.json", lambda d: d["spec"].pop("actors"))
    seen.clear()
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=[LabStub, Picky])
    assert "$.spec: 'actors' is a required property" in info.value.problems[0] and not seen
