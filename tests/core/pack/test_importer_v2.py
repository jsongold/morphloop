"""Pack v2 Importer (#53): the SE pack imports; bad packs list every problem."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from harness.core.pack.v2 import PackV2ImportError, import_pack_v2, validators

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    dest = tmp_path / "pack"
    shutil.copytree(SE_PACK, dest)
    return dest


def _edit(path: Path, edit: object) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)  # type: ignore[operator]
    path.write_text(json.dumps(doc), encoding="utf-8")


def _problems(path: Path) -> str:
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(path)
    return "\n".join(info.value.problems)


def test_se_pack_imports() -> None:
    pack = import_pack_v2(SE_PACK)
    assert pack.pack_id == "software-engineering"
    assert pack.pack_hash.startswith("sha256:")
    assert "network.dns.resolver" in pack.topic_ids
    assert set(pack.documents) == {
        "labels",
        "topics",
        "textbooks",
        "drills",
        "artifacts",
        "llm_roles",
    }
    assert set(pack.llm_roles) == {"assistant", "generator", "judge", "schedule"}
    assert pack.llm_roles["assistant"].prompt_text.startswith("# assistant")
    with pytest.raises(TypeError):
        pack.topics[0]["id"] = "x"  # type: ignore[index]


def test_hash_is_stable_and_content_bound(pack: Path) -> None:
    first = import_pack_v2(SE_PACK).pack_hash
    assert import_pack_v2(SE_PACK).pack_hash == first == import_pack_v2(pack).pack_hash
    _edit(pack / "topics" / "network.json", lambda d: d.update(title="Networks"))
    assert import_pack_v2(pack).pack_hash != first


def test_unlisted_file_is_rejected(pack: Path) -> None:
    (pack / "drills" / "stray.json").write_text("{}", encoding="utf-8")
    assert "drills/stray.json: file is not listed in the manifest" in _problems(pack)


def test_llm_role_missing_prompt_file_is_rejected(pack: Path) -> None:
    _edit(pack / "llm" / "assistant.json", lambda d: d.update(prompt="llm/missing.md"))
    problems = _problems(pack)
    assert "llm/assistant.json: prompt file 'llm/missing.md' does not exist" in problems
    assert "llm/assistant.md: file is not listed in the manifest" in problems


def test_duplicate_llm_role_name_is_rejected(pack: Path) -> None:
    _edit(pack / "llm" / "generator.json", lambda d: d.update(role="assistant"))
    assert "[llm_roles] llm role 'assistant' is declared 2 times" in _problems(pack)


def test_schema_error_names_the_file(pack: Path) -> None:
    _edit(pack / "topics" / "network.json", lambda d: d.pop("title"))
    assert "topics/network.json: $: 'title' is a required property" in _problems(pack)


def test_bad_label_and_duplicate_topic_are_reported_together(pack: Path) -> None:
    drill = pack / "drills" / "dns-answer-nxdomain.json"
    _edit(drill, lambda d: d["labels"].extend(["nope:unknown", "topic:missing"]))
    _edit(
        pack / "topics" / "network.json",
        lambda d: d["topics"].append({"id": "network.dns", "title": "Again"}),
    )
    problems = _problems(pack)
    where = "[labels] drills/dns-answer-nxdomain.json"
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
        assert "[zz_always_fails] software-engineering rejected" in _problems(SE_PACK)
    finally:
        sys.modules.pop(name, None)
