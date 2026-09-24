"""Route auto-discovery for `/v2` (ADR-0018, issue #34/#52).

A resource adds routes to the v0.2 API by dropping
`harness/api/v2/routes/<resource>.py` in this package with a module-level
`router: APIRouter`. :func:`discover_routers` walks every submodule of this
package via `pkgutil` and yields the ones that define it -- nothing here, or
in `harness.api.v2`, keeps a central list to edit.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from fastapi import APIRouter


def discover_routers() -> Iterator[APIRouter]:
    """Import every submodule of this package and yield each one's `router`.

    Re-scans the package directory (and any extra search path a test has
    appended to `__path__`) on every call, so a module dropped onto disk
    between calls is picked up without a process restart.
    """
    package_name = __name__
    for module_info in pkgutil.iter_modules(__path__, prefix=f"{package_name}."):
        module = importlib.import_module(module_info.name)
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            yield router
