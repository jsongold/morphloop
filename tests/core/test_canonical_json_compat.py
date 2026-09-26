"""Byte-identical check: existing JCS implementation vs. ``rfc8785.dumps``.

Issue #18 / docs/backlog/popular-libs-adoption.md item 6: before switching
``harness/core/pack/canonical_json.py`` to delegate to the ``rfc8785`` library,
prove the two implementations produce identical bytes -- the pack content
hash (ADR-0010) is JCS bytes hashed with SHA-256, so any difference changes
every pack's identity.

Result: real pack documents are byte-identical, but the implementations
diverge for integers outside JCS's safe-integer domain (``abs(value) >=
2**53``): the existing implementation falls back to float formatting (and
silently loses precision for values that are not exactly representable as a
double), while ``rfc8785`` raises ``IntegerDomainError``. See
``test_integers_outside_the_safe_range_are_incompatible`` below. Per the
issue, the switch does NOT happen: ``canonical_json.py`` is unchanged.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from harness.core.pack.canonical_json import canonicalize as old_canonicalize
from harness.core.pack.parsing import is_document_path, parse_document

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENTS_DIR = REPO_ROOT / "tests" / "contracts" / "fixtures" / "pack-v2" / "valid"


def _pack_documents() -> list[tuple[str, Any]]:
    paths = sorted(p for p in CONTENTS_DIR.rglob("*") if p.is_file() and is_document_path(p.name))
    return [(str(p.relative_to(REPO_ROOT)), parse_document(p.name, p.read_bytes())) for p in paths]


PACK_DOCUMENTS = _pack_documents()


# --- Real fixtures: every JSON/YAML document of the valid v2 pack fixtures, loaded exactly
# as the importer loads it (harness.core.pack.parsing.parse_document). -------


def test_contents_dir_has_documents_to_compare() -> None:
    # Guards against a silently-empty parametrize list (e.g. a moved fixture dir).
    assert len(PACK_DOCUMENTS) >= 10


@pytest.mark.parametrize(("path", "document"), PACK_DOCUMENTS, ids=[p for p, _ in PACK_DOCUMENTS])
def test_compat_on_every_pack_document(path: str, document: Any) -> None:
    assert old_canonicalize(document) == rfc8785.dumps(document)


# --- Synthetic edge cases: floats, unicode, nesting. -------------------------

_SAFE_EDGE_CASE_VALUES: dict[str, Any] = {
    "null": None,
    "true": True,
    "false": False,
    "empty string": "",
    "empty object": {},
    "empty list": [],
    "zero": 0,
    "negative zero float": -0.0,
    "positive zero float": 0.0,
    "one float": 1.0,
    "small float": 4.5,
    "0.002": 0.002,
    "1e-6": 0.000001,
    "1e-7": 1e-7,
    "1e21": 1e21,
    "1e23": 1e23,
    "123456789012345680000.0": 123456789012345680000.0,
    "333333333.3333333": 333333333.3333333,
    "-1.5e-10": -1.5e-10,
    "int at safe max (2**53 - 1)": 2**53 - 1,
    "int at safe min (-(2**53 - 1))": -(2**53 - 1),
    "negative int": -42,
    "euro sign": "€",
    "carriage return": "\r",
    "control char + accented": 'a"\\\n\x01é',
    "hebrew combining mark": "דּ",
    "surrogate-pair emoji": "\U0001f600",
    "c1 control u+0080": "\u0080",
    "latin with diaeresis": "ö",
    "mixed-key object": {"€": 1, "\r": 2, "דּ": 3, "1": 4, "\U0001f600": 5, "\u0080": 6, "ö": 7},
    "nested mixed": {
        "a": [1, 2.5, "x", None, True, False, {"nested": ["y", {"z": -1.5e-10}]}],
        "b": {"1": 1, "10": 2, "2": 3},
        "": [],
    },
    "list of floats": [0.1, 0.2, 0.3, -0.0, 1e21],
}


@pytest.mark.parametrize(
    ("name", "value"), _SAFE_EDGE_CASE_VALUES.items(), ids=list(_SAFE_EDGE_CASE_VALUES)
)
def test_compat_on_safe_edge_cases(name: str, value: Any) -> None:
    assert old_canonicalize(value) == rfc8785.dumps(value)


# --- Known incompatibility: do NOT switch because of this. -------------------

_UNSAFE_INTEGERS = [2**53, 2**53 + 1, 2**60, -(2**53), -(2**60)]


@pytest.mark.parametrize("value", _UNSAFE_INTEGERS)
def test_integers_outside_the_safe_range_are_incompatible(value: int) -> None:
    """Documents the divergence that blocks the rfc8785 switch (see module docstring).

    The existing implementation keeps returning a value for ``abs(value) >=
    2**53`` (an exact integer string at exactly ``2**53``, or a
    precision-lossy float string beyond it); ``rfc8785.dumps`` raises
    ``IntegerDomainError`` unconditionally for the same input. They are not
    byte-identical -- one path is bytes, the other is an exception.
    """
    old_result = old_canonicalize(value)
    assert old_result  # old implementation happily returns bytes
    with pytest.raises(rfc8785.IntegerDomainError):
        rfc8785.dumps(value)


def test_nan_and_infinity_both_raise_a_value_error() -> None:
    # The one case that stays compatible at the exception-contract level:
    # rfc8785.FloatDomainError subclasses ValueError, same as the existing
    # implementation's plain ValueError.
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            old_canonicalize(value)
        with pytest.raises(ValueError):
            rfc8785.dumps(value)
