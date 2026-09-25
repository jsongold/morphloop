"""Block plaintext: the text a highlight's positions count in (#34, #55).

The one definition of what a learner sees as text in a block body. A highlight's
TextQuoteSelector / TextPositionSelector refer to this string, so every client
(Web, mobile) must produce the same one; ``contracts/fixtures/plaintext/*.json``
pins the rules as input -> output vectors.

Rules (the body is parsed as CommonMark by markdown-it-py's ``commonmark`` preset):

- only the displayed text is kept: emphasis, links and images keep their inner
  text (image: its alt text), markup characters, URLs and titles are dropped;
- inline code and code blocks keep their content verbatim (a code block loses
  only its final newline);
- entities and backslash escapes are decoded;
- a soft or hard line break is ``"\\n"``;
- raw HTML (inline or block) is dropped, its text between tags is kept;
- leaf blocks (paragraphs, headings, code blocks) are joined with ``"\\n"``;
  list markers, quote markers and thematic breaks produce nothing;
- raw HTML blocks keep only their text (tags and comments dropped, entities
  decoded, surrounding whitespace stripped); an HTML block with no text produces nothing;
- a paragraph line that is an ``::artifact{...}`` directive produces nothing (so a
  block that is only a directive has plaintext ``""``; ``"A\\n::artifact{..}\\nB"`` is
  ``"A\\nB"``). Lines inside code blocks or inline code spans are never directives.
  Directive-ness is decided from the line's literal source text, not decoded content
  (:func:`~harness.core.pack.v2.validators.textbook.raw_line_texts`, shared with the
  pack validator): an escaped brace or an HTML entity that decodes to something that
  looks like a directive stays literal text.
"""

from __future__ import annotations

from collections.abc import Sequence
from html.parser import HTMLParser

from markdown_it.token import Token

from harness.core.pack.v2.validators.textbook import (
    ARTIFACT_DIRECTIVE,
    MD,
    inline_lines,
    raw_line_texts,
)


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts).strip()


def _paragraph(token: Token, source_lines: Sequence[str]) -> str | None:
    """Inline text without directive lines; ``None`` if every line is a directive."""
    lines = list(inline_lines(token.children or ()))
    raws = raw_line_texts(source_lines, token)
    kept = [
        _inline(line)
        for line, raw in zip(lines, raws, strict=True)
        if raw is None or not ARTIFACT_DIRECTIVE.fullmatch(raw)
    ]
    return "\n".join(kept) if kept else None


def _inline(children: Sequence[Token]) -> str:
    out: list[str] = []
    for token in children:
        if token.type in ("text", "text_special", "code_inline"):
            out.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            out.append("\n")
        elif token.type == "image":
            out.append(_inline(token.children or ()))
    return "".join(out)


def block_plaintext(body: str) -> str:
    """The plaintext of one block body (CommonMark)."""
    chunks: list[str] = []
    source_lines = body.splitlines()
    for token in MD.parse(body):
        if token.type == "inline":
            if (text := _paragraph(token, source_lines)) is not None:
                chunks.append(text)
        elif token.type in ("fence", "code_block"):
            chunks.append(token.content.removesuffix("\n"))
        elif token.type == "html_block" and (text := _html_text(token.content)):
            chunks.append(text)
    return "\n".join(chunks)
