"""Every ``::artifact{type=<type> ref=<id>}`` line in a textbook block names a pack artifact.

The directive is a whole line of paragraph text in a block body (CommonMark; lines
inside code blocks or inline code spans are not directives), exactly
``::artifact{type=T ref=R}``.
``ref`` must be an artifact id of the pack and ``type`` that artifact's type. A line
whose literal source starts with ``::artifact`` but is not exactly this form is a
problem too.

Directive-ness is decided from the line's literal source text (found via the inline
token's ``map`` line range, not from decoded/rendered content): an escaped brace
(``::artifact\\{...}``) or an HTML entity (``&#58;&#58;artifact{...}``) can decode to
something that *looks* like a directive, but the raw source never reads
``::artifact{...}`` there, so it is plain text, not a directive (:func:`raw_line_texts`;
shared with :mod:`harness.core.textbook.plaintext` so both agree).
Block ids are unique within a doc and doc ids are unique across the pack.
(Topic coverage of textbook docs is checked by :mod:`.topics`.)
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt
from markdown_it.token import Token

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

ARTIFACT_DIRECTIVE = re.compile(r"::artifact\{type=([^\s{}]+) ref=([^\s{}]+)\}")
"""One directive line (use ``fullmatch`` on the stripped line); groups are type, ref."""

MD = MarkdownIt("commonmark")
"""The CommonMark parser block bodies are read with (shared with textbook plaintext)."""


def inline_lines(children: Sequence[Token]) -> Iterator[list[Token]]:
    """An inline token's children split into lines at soft/hard breaks (breaks dropped)."""
    line: list[Token] = []
    for token in children:
        if token.type in ("softbreak", "hardbreak"):
            yield line
            line = []
        else:
            line.append(token)
    yield line


def raw_line_texts(body: str, token: Token) -> list[str | None]:
    """Literal (undecoded) source text of an inline token's split lines, stripped.

    One entry per line yielded by :func:`inline_lines`, aligned by index; ``None`` for
    a line that cannot be read 1:1 off the raw source because a multiline token (e.g.
    a code span whose backticks are on other lines) swallowed a line break inside this
    paragraph without producing a soft/hard break.
    """
    lines = list(inline_lines(token.children or ()))
    start, end = token.map or (0, 0)
    if end - start != len(lines):
        return [None] * len(lines)
    source = body.splitlines()
    return [source[start + i].strip() for i in range(len(lines))]


def text_lines(body: str) -> Iterator[str]:
    """Literal source text of each inline line (paragraphs, headings) that maps 1:1 to
    a raw source line; used only to find literal ``::artifact`` directives, never code.
    """
    for token in MD.parse(body):
        if token.type == "inline":
            for raw in raw_line_texts(body, token):
                if raw is not None:
                    yield raw


def validate(pack: PackV2) -> Iterable[str]:
    types = {str(a["id"]): str(a["type"]) for a in pack.documents.get("artifacts", {}).values()}
    docs = pack.documents.get("textbooks", {})
    problems = [
        f"textbook doc id {i!r} appears {n} times"
        for i, n in sorted(Counter(str(d["id"]) for d in docs.values()).items())
        if n > 1
    ]
    for path, doc in docs.items():
        raw = doc["blocks"]
        assert isinstance(raw, Sequence)
        blocks = [b for b in raw if isinstance(b, Mapping)]  # schema-checked already
        problems += [
            f"{path}: block id {i!r} appears {n} times"
            for i, n in sorted(Counter(str(b["id"]) for b in blocks).items())
            if n > 1
        ]
        for block in blocks:
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
