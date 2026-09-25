"""Pack v2 validator: textbook ``::artifact{}`` directives resolve to pack artifacts (#55)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from harness.core.pack.v2 import PackV2ImportError, import_pack_v2

SE_PACK = Path(__file__).resolve().parents[3] / "contents" / "v2" / "software-engineering"
DOC = "textbooks/network.dns.lookup-path.json"


def _problems_with_body(tmp_path: Path, body: str) -> str:
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][2]["body"] = body
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack)
    return "\n".join(p for p in info.value.problems if p.startswith("[textbook]"))


def test_se_pack_directives_resolve() -> None:
    import_pack_v2(SE_PACK)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("::artifact{type=diagram ref=missing}", "artifact 'missing' is not in the pack"),
        ("::artifact{type=lab ref=dns-resolution-flow}", "is 'diagram', not 'lab'"),
        ("Text.\n::artifact{ref=dns-resolution-flow}", "malformed directive"),
        (
            "::artifact{type=diagram ref=dns-resolution-flow} **bold**",
            "malformed directive",
        ),
        (
            "::artifact{type=diagram ref=dns-resolution-flow} `code`",
            "malformed directive",
        ),
        (
            "::artifact{type=diagram ref=dns-resolution-flow} <b>html</b>",
            "malformed directive",
        ),
        (
            "::artifact\\{type=diagram ref=dns-resolution-flow}",
            "malformed directive",
        ),
    ],
)
def test_bad_directive_is_refused(tmp_path: Path, body: str, expected: str) -> None:
    problems = _problems_with_body(tmp_path, body)
    assert f"{DOC}#diagram: " in problems
    assert expected in problems


def test_entity_encoded_lookalike_is_not_a_directive(tmp_path: Path) -> None:
    """``&#58;&#58;artifact{...}`` decodes to look like a directive but never is one:
    directive-ness is decided from the literal source line, not decoded content."""
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][2]["body"] = "&#58;&#58;artifact{type=lab ref=missing}"
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack)


@pytest.mark.parametrize(
    "body",
    [
        "```\n::artifact{type=lab ref=missing}\n```",
        "Example:\n\n    ::artifact{type=lab ref=missing}",
    ],
)
def test_directive_in_code_block_is_ignored(tmp_path: Path, body: str) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][2]["body"] = body
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack)


def test_multiline_code_span_directive_is_ignored(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][2]["body"] = "Try `\n::artifact{type=lab ref=missing}\n` here."
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack)


def test_duplicate_block_id_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][1]["id"] = doc["blocks"][0]["id"]
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack)
    assert any(f"{DOC}: block id 'summary' appears 2 times" in p for p in info.value.problems)


def test_duplicate_doc_id_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(SE_PACK, pack)
    other = pack / "textbooks/network.dns.answers.json"
    doc = json.loads(other.read_text(encoding="utf-8"))
    doc["id"] = "network.dns.lookup-path"
    other.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack)
    assert any(
        "textbook doc id 'network.dns.lookup-path' appears 2 times" in p
        for p in info.value.problems
    )
