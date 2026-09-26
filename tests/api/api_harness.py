"""The real SDK app for API tests, with no external service wired.

``create_app`` wires nothing at startup: the `/v2` dependencies build their
Postgres store lazily, and tests swap them for in-memory fakes through
``app.state`` or ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI

from harness.api.app import AppExtension, create_app


def build_app(*, extensions: Sequence[AppExtension] = ()) -> FastAPI:
    """The real FastAPI app with ``extensions``."""
    return create_app(extensions=extensions)
