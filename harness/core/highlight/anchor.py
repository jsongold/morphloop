"""The one highlight anchor rule JSON Schema cannot express (#34, #60).

The anchor's shape (a W3C TextQuoteSelector + TextPositionSelector, in that
order) is checked by the event payload contract
(``contracts/schemas/events/payloads/highlight.created/1.json``) and mirrored
in the API's pydantic request model. JSON Schema cannot compare two sibling
properties of one object, so the TextPositionSelector's ``start < end`` is
checked here instead, and called from the pydantic model's validator.

Whether the selector actually matches the block's plaintext is out of scope
for v0.2.0 (issue #34): "this PR checks shape and start<end only".
"""

from __future__ import annotations


class AnchorError(ValueError):
    """A TextPositionSelector has ``start >= end``."""


def check_text_position(start: int, end: int) -> None:
    """Raise :class:`AnchorError` unless ``start < end``."""
    if start >= end:
        raise AnchorError(f"TextPositionSelector start ({start}) must be < end ({end})")
