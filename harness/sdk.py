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

The rest of the surface is what an artifact type with its own routes needs: the
v2 event store Port and its value types, the ``View`` base, the per-request
FastAPI dependencies of ``/v2`` (transaction, pack, user, event id, ``ws_or_404``),
the problem response, the lab runtime / terminal bridge Ports with their Docker
adapters, the domain adapter registry and the contract schemas. Test support
(fakes, an in-memory store, contract validation) is ``harness.testing``. Anything
not exported here is internal and may change between SDK versions.
"""

from harness.adapters.docker_lab import DockerLabRuntime
from harness.adapters.pty import DockerTerminalBridge
from harness.api.app import AppExtension, create_app
from harness.api.problems import problem
from harness.api.v2.deps import (
    EventIdDep,
    EventTransactionV2Dep,
    PackV2Dep,
    UserIdDep,
    build_event_store_v2,
    user_id_of,
    ws_or_404,
)
from harness.core.artifact import (
    Artifact,
    UnknownArtifactTypeError,
    artifact_class,
    registered_artifact_types,
)
from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    AdapterParamsError,
    CommandDetector,
    DetectedCommand,
    DomainAdapterError,
    DomainAdapterRegistry,
    TerminalTool,
    run_check,
)
from harness.core.pack.v2 import PackV2, PackV2ImportError, import_pack_v2
from harness.core.ports.events_v2 import (
    ActorV2,
    EventIdConflictError,
    EventStoreV2,
    EventTransactionV2,
    EventV2,
    StoredEventV2,
    ViewDocumentStore,
)
from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_json, to_plain_object
from harness.core.ports.lab_runtime import (
    ExecRequest,
    ExecResult,
    ImageRef,
    LabInfo,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    ResourceLimits,
)
from harness.core.ports.terminal_bridge import (
    TerminalBridge,
    TerminalBridgeError,
    TerminalOpenRequest,
    TerminalSession,
    TerminalSize,
)
from harness.core.view import View, dispatch

__all__ = [
    # app assembly, artifact types, contracts
    "AppExtension",
    "Artifact",
    "ContractSchemas",
    "PackV2",
    "PackV2ImportError",
    "UnknownArtifactTypeError",
    "artifact_class",
    "create_app",
    "import_pack_v2",
    "registered_artifact_types",
    # /v2 request dependencies and responses
    "EventIdDep",
    "EventTransactionV2Dep",
    "PackV2Dep",
    "UserIdDep",
    "build_event_store_v2",
    "problem",
    "user_id_of",
    "ws_or_404",
    # events and views
    "ActorV2",
    "EventIdConflictError",
    "EventStoreV2",
    "EventTransactionV2",
    "EventV2",
    "StoredEventV2",
    "View",
    "ViewDocumentStore",
    "dispatch",
    # JSON value types
    "JsonObject",
    "PlainJson",
    "to_plain_json",
    "to_plain_object",
    # labs, terminals and their Docker adapters
    "DockerLabRuntime",
    "DockerTerminalBridge",
    "ExecRequest",
    "ExecResult",
    "ImageRef",
    "LabInfo",
    "LabRuntime",
    "LabRuntimeError",
    "LabSpec",
    "ResourceLimits",
    "TerminalBridge",
    "TerminalBridgeError",
    "TerminalOpenRequest",
    "TerminalSession",
    "TerminalSize",
    # domain adapters
    "AdapterParamsError",
    "CommandDetector",
    "DetectedCommand",
    "DomainAdapterError",
    "DomainAdapterRegistry",
    "TerminalTool",
    "run_check",
]
