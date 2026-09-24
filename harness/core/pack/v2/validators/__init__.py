"""Cross-file validators for pack v2, one module per kind (#53).

Putting a module ``<kind>.py`` in this package registers it: the module defines
``validate(pack: PackV2) -> Iterable[str]`` returning one message per problem
(empty when the pack is fine). Validators run after every file passed its
schema, in module name order, and see the whole :class:`PackV2`.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2

type Validator = Callable[[PackV2], Iterable[str]]


def registered() -> dict[str, Validator]:
    """Module name -> ``validate`` of every module in this package."""
    out: dict[str, Validator] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{__name__}.{info.name}")
        validate = getattr(module, "validate", None)
        if not callable(validate):
            raise TypeError(f"validator module {module.__name__} must define validate(pack)")
        out[info.name] = validate
    return out
