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
- a paragraph that is an ``::artifact{...}`` directive produces nothing (so a
  block that is only a directive has plaintext ``""``).
"""

from __future__ import annotations

from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from harness.core.pack.v2.validators.textbook import ARTIFACT_DIRECTIVE

_MD = MarkdownIt("commonmark")


def _inline(children: Sequence[Token]) -> str:
    out: list[str] = []
    for token in children:
        if token.type in ("text", "code_inline"):
            out.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            out.append("\n")
        elif token.type == "image":
            out.append(_inline(token.children or ()))
    return "".join(out)


def block_plaintext(body: str) -> str:
    """The plaintext of one block body (CommonMark)."""
    chunks: list[str] = []
    for token in _MD.parse(body):
        if token.type == "inline":
            if not ARTIFACT_DIRECTIVE.fullmatch(token.content.strip()):
                chunks.append(_inline(token.children or ()))
        elif token.type in ("fence", "code_block"):
            chunks.append(token.content.removesuffix("\n"))
    return "\n".join(chunks)
