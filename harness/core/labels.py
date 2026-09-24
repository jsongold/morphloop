"""Label syntax and vocabulary check (#34, #48).

Same rules as ``contracts/schemas/pack/v2/defs.json`` (``$defs/label``) plus
the checks a schema cannot make:

- a label is ``name`` or ``namespace:name``, lowercase;
- ``sys:`` is reserved; the only one is ``sys:holdout``;
- ``topic:<id>`` must name a topic in the given topic id set;
- every other label must be in the pack's vocabulary.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

HOLDOUT = "sys:holdout"
RESERVED_LABELS = frozenset({HOLDOUT})
TOPIC_PREFIX = "topic:"

_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}(:[a-z0-9][a-z0-9._-]{0,127})?")


class LabelError(ValueError):
    """Some labels are invalid; ``offending`` maps each one to the reason."""

    def __init__(self, offending: dict[str, str]) -> None:
        self.offending = offending
        details = "; ".join(f"{label!r}: {why}" for label, why in sorted(offending.items()))
        super().__init__(f"invalid labels: {details}")


def label_problems(
    labels: Iterable[str], *, vocabulary: Collection[str], topic_ids: Collection[str]
) -> dict[str, str]:
    """Return ``{label: reason}`` for every invalid label (empty when all are valid)."""
    problems: dict[str, str] = {}
    for label in labels:
        if not _LABEL.fullmatch(label):
            problems[label] = "not 'name' or 'namespace:name' (lowercase)"
        elif label.startswith("sys:"):
            if label not in RESERVED_LABELS:
                problems[label] = f"unknown reserved label (only {HOLDOUT})"
        elif label.startswith(TOPIC_PREFIX):
            if label.removeprefix(TOPIC_PREFIX) not in topic_ids:
                problems[label] = "topic id not in the topic tree"
        elif label not in vocabulary:
            problems[label] = "not in the pack vocabulary"
    return problems


def check_labels(
    labels: Iterable[str], *, vocabulary: Collection[str], topic_ids: Collection[str]
) -> None:
    """Raise :class:`LabelError` listing every invalid label."""
    problems = label_problems(labels, vocabulary=vocabulary, topic_ids=topic_ids)
    if problems:
        raise LabelError(problems)
