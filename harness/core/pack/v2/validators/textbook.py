"""Every ``::artifact{type=<type> ref=<id>}`` line in a textbook block names a pack artifact.

The directive is a whole line of paragraph text in a block body (CommonMark; lines
inside code blocks are not directives), exactly ``::artifact{type=T ref=R}``.
``ref`` must be an artifact id of the pack and ``type`` that artifact's type. A line
that starts with ``::artifact`` but is not this form is a problem too.
(Topic coverage of textbook docs is checked by :mod:`.topics`.)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

ARTIFACT_DIRECTIVE = re.compile(r"::artifact\{type=([^\s{}]+) ref=([^\s{}]+)\}")
"""One directive line (use ``fullmatch`` on the stripped line); groups are type, ref."""

MD = MarkdownIt("commonmark")
"""The CommonMark parser block bodies are read with (shared with textbook plaintext)."""


def text_lines(body: str) -> Iterator[str]:
    """Stripped source lines of the body's inline text (paragraphs, headings), not code."""
    for token in MD.parse(body):
        if token.type == "inline":
            yield from (line.strip() for line in token.content.split("\n"))


def validate(pack: PackV2) -> Iterable[str]:
    types = {str(a["id"]): str(a["type"]) for a in pack.documents.get("artifacts", {}).values()}
    problems: list[str] = []
    for path, doc in pack.documents.get("textbooks", {}).items():
        blocks = doc["blocks"]
        assert isinstance(blocks, Sequence)
        for block in blocks:
            assert isinstance(block, Mapping)
            where = f"{path}#{block['id']}"
            for line in text_lines(str(block["body"])):
                if not line.startswith("::artifact"):
                    continue
                match = ARTIFACT_DIRECTIVE.fullmatch(line)
                if match is None:
                    problems.append(f"{where}: malformed directive {line!r}")
                    continue
                kind, ref = match.groups()
                if ref not in types:
                    problems.append(f"{where}: artifact {ref!r} is not in the pack")
                elif types[ref] != kind:
                    problems.append(f"{where}: artifact {ref!r} is {types[ref]!r}, not {kind!r}")
    return problems
