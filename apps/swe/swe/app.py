"""The SWE API server: the SDK app plus this app's extension (#95).

``uvicorn --factory swe.app:create_swe_app`` (docker-compose) serves it. The pack
is this app's own (:data:`swe.PACK_DIR`) unless ``MORPHLOOP_PACK_V2_DIR`` names
another one.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from harness.sdk import AppExtension, create_app, import_pack_v2
from swe import PACK_DIR
from swe.artifacts.diagram import DiagramArtifact
from swe.artifacts.lab import LabArtifact
from swe.artifacts.lab import routes as lab_routes
from swe.artifacts.lab import socket as lab_socket

EXTENSION = AppExtension(
    routers=(lab_routes.router, lab_socket.router),
    artifact_types=(LabArtifact, DiagramArtifact),
)


def create_swe_app() -> FastAPI:
    """The SDK app with the SWE extension."""
    app = create_app(extensions=[EXTENSION])
    if not os.environ.get("MORPHLOOP_PACK_V2_DIR"):
        app.state.pack_v2 = import_pack_v2(PACK_DIR, artifact_types=EXTENSION.artifact_types)
    return app
