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
``::artifact{...}`` there, so it is plain text, not a directive. A leading
blockquote/heading marker is always stripped from that literal text first (both
can interrupt a paragraph unconditionally in CommonMark); a leading list marker is
stripped only where markdown-it actually opened a list item on that line — an
ordered list starting at a number other than 1 does not interrupt a preceding
paragraph, so e.g. ``"2. x"`` right after plain text is literal text, not a list
item (:func:`raw_line_texts`, :func:`list_item_open_lines`; shared with
:mod:`harness.core.textbook.plaintext` so both agree).
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


_QUOTE_OR_HEADING_PREFIX = re.compile(r"^(?:>[ \t]?|#{1,6}[ \t]+)")
"""A blockquote or ATX-heading marker: both can interrupt a paragraph unconditionally
in CommonMark, so one at a line's start always unambiguously opens/continues it."""

_LIST_MARKER_PREFIX = re.compile(r"^(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+)")
"""A bullet/ordered-list marker: only safe to strip on a line where markdown-it
actually opened a list item there (see :func:`list_item_open_lines`) — an ordered
list whose start number isn't 1 cannot interrupt a paragraph, so e.g. ``"2. x"``
right after plain text is literal paragraph text, not a list item."""


def list_item_open_lines(tokens: Iterable[Token]) -> frozenset[int]:
    """0-based source lines where a ``list_item_open`` token starts.

    A list marker appears in the raw source only on a list item's first line, and
    only when CommonMark actually opened that item there (e.g. an ordered list
    starting at a number other than 1 does not interrupt a preceding paragraph, so
    no ``list_item_open`` is emitted and its would-be marker line is plain text).
    """
    return frozenset(t.map[0] for t in tokens if t.type == "list_item_open" and t.map)


def _strip_block_prefix(text: str, *, at_list_item_open: bool) -> str:
    """Strip leading blockquote/list/heading markers (repeated, for nesting).

    Blockquote/heading markers are always safe to strip (they interrupt a
    paragraph unconditionally). A list marker is stripped only when
    ``at_list_item_open`` says markdown-it actually opened a list item on this
    line — otherwise it's literal paragraph text that merely looks like one.
    """
    while True:
        if match := _QUOTE_OR_HEADING_PREFIX.match(text):
            text = text[match.end() :]
            continue
        if at_list_item_open and (match := _LIST_MARKER_PREFIX.match(text)):
            text = text[match.end() :]
            continue
        break
    return text


def raw_line_texts(
    source_lines: Sequence[str], token: Token, list_item_open_lines: frozenset[int]
) -> list[str | None]:
    """Literal (undecoded) source text of an inline token's split lines, with any
    blockquote/list/heading prefix stripped, so ``::artifact`` can be told apart from
    an escape/entity that merely decodes to look like one.

    One entry per line yielded by :func:`inline_lines`, aligned by index; ``None`` for
    a line that cannot be read off the raw source because a multiline token (e.g. a
    code span whose backticks are on other lines) swallowed one or more line breaks
    without producing a soft/hard break. Lines strictly before the first such token and
    strictly after the last are still mapped, each from its own end of the range.
    """
    lines = list(inline_lines(token.children or ()))
    start, end = token.map or (0, 0)
    n = len(lines)

    def strip(line_no: int) -> str:
        return _strip_block_prefix(
            source_lines[line_no].strip(), at_list_item_open=line_no in list_item_open_lines
        )

    if end - start == n:
        return [strip(start + i) for i in range(n)]

    multiline_capable = {"code_inline", "html_inline"}
    is_complex = [any(t.type in multiline_capable for t in line) for line in lines]
    if not any(is_complex):
        return [None] * n  # unreachable (would imply end - start == n), kept for safety
    first, last = is_complex.index(True), len(is_complex) - 1 - is_complex[::-1].index(True)
    result: list[str | None] = [None] * n
    for i in range(first):
        result[i] = strip(start + i)
    for i in range(last + 1, n):
        result[i] = strip(end - n + i)
    return result


def text_lines(body: str) -> Iterator[str]:
    """Literal source text of each inline line (paragraphs, headings) that maps to a
    raw source line; used only to find literal ``::artifact`` directives, never code.
    """
    source_lines = body.splitlines()
    tokens = MD.parse(body)
    open_lines = list_item_open_lines(tokens)
    for token in tokens:
        if token.type == "inline":
            for raw in raw_line_texts(source_lines, token, open_lines):
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
