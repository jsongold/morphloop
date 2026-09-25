"""The public SDK surface for app authors (#95, ADR-0018 §19).

An app (``apps/<app>/``, later its own repository) imports only this module::

    from harness.sdk import AppExtension, Artifact, create_app

    class LabArtifact(Artifact):
        type = "lab"                      # the ``type`` a pack artifact names
        spec_schema = {...}               # JSON Schema (2020-12) of its ``spec``

        @classmethod
        def validate_spec(cls, spec, pack):   # optional cross-file rules
            return [...]                      # one message per problem

    app = create_app(extensions=[AppExtension(routers=(router,), artifact_types=(LabArtifact,))])

``create_app`` mounts the routers under ``/v2`` and hands the artifact types to the
pack importer, which refuses an artifact of any other ``type`` and validates each
``spec`` with the type's schema and validator. ``import_pack_v2(path,
artifact_types=[...])`` does the same outside a server.

Anything not exported here is internal and may change between SDK versions.
"""

from harness.api.app import AppExtension, create_app
from harness.core.artifact import (
    Artifact,
    UnknownArtifactTypeError,
    artifact_class,
    registered_artifact_types,
)
from harness.core.pack.v2 import PackV2, PackV2ImportError, import_pack_v2

__all__ = [
    "AppExtension",
    "Artifact",
    "PackV2",
    "PackV2ImportError",
    "UnknownArtifactTypeError",
    "artifact_class",
    "create_app",
    "import_pack_v2",
    "registered_artifact_types",
]
