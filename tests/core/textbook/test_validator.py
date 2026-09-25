"""Pack v2 validator: textbook ``::artifact{}`` directives resolve to pack artifacts (#55)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pack_artifact_types import PACK_ARTIFACT_TYPES

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
        import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
    return "\n".join(p for p in info.value.problems if p.startswith("[textbook]"))


def test_se_pack_directives_resolve() -> None:
    import_pack_v2(SE_PACK, artifact_types=PACK_ARTIFACT_TYPES)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("::artifact{type=diagram ref=missing}", "artifact 'missing' is not in the pack"),
        ("::artifact{type=lab ref=dns-resolution-flow}", "is 'diagram', not 'lab'"),
        ("Text.\n::artifact{ref=dns-resolution-flow}", "malformed directive"),
    ],
)
def test_bad_directive_is_refused(tmp_path: Path, body: str, expected: str) -> None:
    problems = _problems_with_body(tmp_path, body)
    assert f"{DOC}#diagram: " in problems
    assert expected in problems


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
    import_pack_v2(pack, artifact_types=PACK_ARTIFACT_TYPES)
