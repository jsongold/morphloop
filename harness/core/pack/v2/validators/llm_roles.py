"""LLM role rules the schema cannot express: role names are unique across files.

Prompt file existence is checked by the Importer itself (it must read the file
to build ``PackV2.llm_roles``, before any validator sees the pack).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.pack.v2.importer import PackV2


def validate(pack: PackV2) -> Iterable[str]:
    roles = Counter(str(doc["role"]) for doc in pack.documents["llm_roles"].values())
    return [
        f"llm role {role!r} is declared {n} times" for role, n in sorted(roles.items()) if n > 1
    ]
