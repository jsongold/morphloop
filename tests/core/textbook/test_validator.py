"""Pack v2 validator: textbook ``::artifact{}`` directives resolve to pack artifacts (#55)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

from harness.core.pack.v2 import PackV2ImportError, import_pack_v2

PACK = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "pack-v2"
    / "valid"
    / "dns-pack"
)
DOC = "textbooks/dns-resolution.json"
BLOCK = 1  # the block whose body is a directive


def _problems_with_body(tmp_path: Path, body: str) -> str:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][BLOCK]["body"] = body
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
    return "\n".join(p for p in info.value.problems if p.startswith("[textbook]"))


def test_pack_directives_resolve() -> None:
    import_pack_v2(PACK, artifact_types=PACK_ARTIFACT_TYPES)


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
        (
            "> ::artifact{type=diagram ref=missing}",
            "artifact 'missing' is not in the pack",
        ),
        (
            "- ::artifact{type=diagram ref=missing}",
            "artifact 'missing' is not in the pack",
        ),
        (
            "# ::artifact{type=diagram ref=missing}",
            "artifact 'missing' is not in the pack",
        ),
        (
            "Try `\nx\ny` code.\n::artifact{type=diagram ref=missing}",
            "artifact 'missing' is not in the pack",
        ),
    ],
)
def test_bad_directive_is_refused(tmp_path: Path, body: str, expected: str) -> None:
    problems = _problems_with_body(tmp_path, body)
    assert f"{DOC}#b2: " in problems
    assert expected in problems


def test_text_lines_splits_source_once_per_body() -> None:
    """``text_lines`` must not re-split the body once per paragraph (#94 perf)."""
    from harness.core.pack.v2.validators.textbook import text_lines

    calls = 0
    real_splitlines = str.splitlines

    class _CountingStr(str):
        def splitlines(self, *args: object, **kwargs: object) -> list[str]:
            nonlocal calls
            calls += 1
            return real_splitlines(self, *args, **kwargs)

    body = _CountingStr("\n\n".join(f"Paragraph {i}." for i in range(20)))
    assert list(text_lines(body)) == [f"Paragraph {i}." for i in range(20)]
    assert calls <= 1


def test_entity_encoded_lookalike_is_not_a_directive(tmp_path: Path) -> None:
    """``&#58;&#58;artifact{...}`` decodes to look like a directive but never is one:
    directive-ness is decided from the literal source line, not decoded content."""
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][BLOCK]["body"] = "&#58;&#58;artifact{type=lab ref=missing}"
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)


@pytest.mark.parametrize(
    "body",
    [
        "```\n::artifact{type=lab ref=missing}\n```",
        "Example:\n\n    ::artifact{type=lab ref=missing}",
    ],
)
def test_directive_in_code_block_is_ignored(tmp_path: Path, body: str) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][BLOCK]["body"] = body
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)


def test_multiline_code_span_directive_is_ignored(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][BLOCK]["body"] = "Try `\n::artifact{type=lab ref=missing}\n` here."
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)


def test_duplicate_block_id_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    doc = json.loads((pack / DOC).read_text(encoding="utf-8"))
    doc["blocks"][1]["id"] = doc["blocks"][0]["id"]
    (pack / DOC).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
    assert any(f"{DOC}: block id 'b1' appears 2 times" in p for p in info.value.problems)


def test_duplicate_doc_id_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    shutil.copytree(PACK, pack)
    # A second manifest-listed textbook file that reuses the same doc id.
    (pack / "textbooks/dns-resolution-copy.json").write_text(
        (pack / DOC).read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    manifest["textbooks"].append("textbooks/dns-resolution-copy.json")
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackV2ImportError) as info:
        import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
    assert any("textbook doc id 'dns-resolution' appears 2 times" in p for p in info.value.problems)
