"""The v0.2 HTTP API root: `/v2` (ADR-0018, issue #34/#52).

`build_v2_router` mounts every resource route module found under
`harness.api.v2.routes` (see that package for how a resource adds routes).
It is called once per `create_app()` (`harness.api.app`), not cached at
import time, so newly discovered route modules are picked up by the next app
build -- the property `tests/api/v2/test_router_autoinclude.py` relies on.
"""

from __future__ import annotations

from fastapi import APIRouter

from harness.api.v2.routes import discover_routers


def build_v2_router() -> APIRouter:
    """A fresh `/v2` router with every resource route module included."""
    router = APIRouter(prefix="/v2")
    for resource_router in discover_routers():
        router.include_router(resource_router)
    return router
