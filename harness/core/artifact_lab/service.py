"""Start, reset, stop and check lab artifacts (#62).

Uses only the :class:`LabRuntime` Port and the
domain adapter registry; never an adapter. Every change is one v2 event appended
with its view update in one transaction (ADR-0008). The ws is an opaque id.

A lab lives until an explicit stop or, when its spec declares ``idle_seconds``,
until its ws has had no non-system event for that long
(:meth:`LabArtifactService.reap_idle`); the runtime's ``lifetime_seconds`` stays
the backstop. The ws's session comes from its ``ws.created`` event. A resent
event id returns the stored result instead of acting twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from harness.core.artifact_lab.lab import ArtifactView, LabArtifact
from harness.core.domain_adapter import (
    AdapterParamsError,
    DomainAdapterError,
    DomainAdapterRegistry,
    run_check,
)
from harness.core.ports.events_v2 import (
    ActorV2,
    EventIdConflictError,
    EventStoreV2,
    EventV2,
    StoredEventV2,
)
from harness.core.ports.json_types import JsonObject, PlainJson, to_plain_json, to_plain_object
from harness.core.ports.lab_runtime import ImageRef, LabInfo, LabRuntime, LabRuntimeError, LabSpec
from harness.core.view import dispatch


class ArtifactError(Exception):
    """A request the service refuses; ``status`` / ``code`` are the problem response."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def _not_found(detail: str) -> ArtifactError:
    return ArtifactError(404, "not-found", detail)


def _invalid(detail: str) -> ArtifactError:
    return ArtifactError(422, "validation-failed", detail)


def _unavailable(detail: str) -> ArtifactError:
    return ArtifactError(503, "lab-unavailable", detail)


def _key_reused(event_id: str) -> ArtifactError:
    return ArtifactError(
        409, "idempotency-key-reused", f"event id {event_id!r} was used differently"
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(slots=True, kw_only=True)
class LabArtifactService:
    """The runtime parts; the pack's artifact specs (``pack/v2/artifact-spec.json``
    by id) are passed per call, so one service serves whichever pack is wired."""

    store: EventStoreV2
    labs: LabRuntime
    adapters: DomainAdapterRegistry
    now: Callable[[], datetime] = field(default=_utc_now)
    new_id: Callable[[str], str] = field(default=_new_id)

    # --- reads --------------------------------------------------------------

    def get(
        self, artifact_id: str, *, user_id: str | None = None, ws_id: str | None = None
    ) -> JsonObject:
        """The ``artifact`` view document; 404 if unknown or not the user's / the ws's."""
        with self.store.transaction() as tx:
            document = ArtifactView.get(tx, artifact_id)
        if (
            document is None
            or (user_id is not None and document["user_id"] != user_id)
            or (ws_id is not None and document["ws_id"] != ws_id)
        ):
            raise _not_found(f"artifact {artifact_id!r} not found")
        return document

    def _running(self, artifact_id: str, user_id: str | None, ws_id: str | None) -> JsonObject:
        document = self.get(artifact_id, user_id=user_id, ws_id=ws_id)
        if document["status"] != "running":
            raise ArtifactError(409, "state-conflict", f"artifact {artifact_id!r} is stopped")
        return document

    @staticmethod
    def _lab(document: JsonObject, specs: Mapping[str, JsonObject]) -> tuple[LabArtifact, str]:
        spec = specs.get(str(document["spec_id"]))
        lab = document["lab"]
        if spec is None or not isinstance(lab, Mapping):
            raise _not_found(f"artifact spec {document['spec_id']!r} is no longer available")
        artifact = LabArtifact.from_spec(str(document["artifact_id"]), spec)
        return artifact, str(lab["lab_instance_id"])

    # --- lifecycle ----------------------------------------------------------

    def start(
        self,
        specs: Mapping[str, JsonObject],
        *,
        user_id: str,
        ws_id: str,
        spec_id: str,
        event_id: str,
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Start a lab from ``spec_id`` in ``ws_id`` (``artifact.started``)."""
        replay = self._replay(ws_id, event_id, "artifact.started", {"spec_id": spec_id})
        if replay is not None:
            return self.get(str(replay.payload["artifact_id"]))
        scope = (user_id, self._session_of(user_id, ws_id), ws_id)
        spec = specs.get(spec_id)
        if spec is None:
            raise _not_found(f"artifact spec {spec_id!r} not found")
        artifact_id = self.new_id("art")
        try:
            artifact = LabArtifact.from_spec(artifact_id, spec)
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
        lab_instance_id = self.new_id("lab")
        info = self._run_lab(lambda s: self.labs.start(lab_instance_id, s), artifact)
        payload: dict[str, PlainJson] = {
            "artifact_id": artifact_id,
            "type": LabArtifact.type,
            "spec_id": spec_id,
            "lab": self._lab_payload(info, artifact),
        }
        try:
            self._append(scope, event_id, "artifact.started", actor, payload)
        except Exception:
            self.labs.destroy(lab_instance_id)
            raise
        return self.get(artifact_id)

    def reset(
        self,
        specs: Mapping[str, JsonObject],
        artifact_id: str,
        *,
        event_id: str,
        user_id: str | None = None,
        ws_id: str | None = None,
    ) -> JsonObject:
        """Replace the lab with a fresh one from the same spec (``artifact.reset``)."""
        if self._replay(ws_id, event_id, "artifact.reset", {"artifact_id": artifact_id}):
            return self.get(artifact_id)
        document = self._running(artifact_id, user_id, ws_id)
        artifact, old = self._lab(document, specs)
        new = self.new_id("lab")
        info = self._run_lab(
            lambda s: self.labs.reset(old, new_lab_instance_id=new, spec=s), artifact
        )
        payload: dict[str, PlainJson] = {
            "artifact_id": artifact_id,
            "replaces_lab_instance_id": old,
            "lab": self._lab_payload(info, artifact),
        }
        self._append(_scope_of(document), event_id, "artifact.reset", "learner", payload)
        return self.get(artifact_id)

    def stop(
        self,
        artifact_id: str,
        *,
        event_id: str,
        user_id: str | None = None,
        ws_id: str | None = None,
        reason: str = "requested",
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Record ``artifact.stopped``, then destroy the lab (best effort; lifetime backstops)."""
        if self._replay(ws_id, event_id, "artifact.stopped", {"artifact_id": artifact_id}):
            return self.get(artifact_id)
        document = self._running(artifact_id, user_id, ws_id)
        lab = document["lab"]
        assert isinstance(lab, Mapping)
        payload = {"artifact_id": artifact_id, "reason": reason}
        self._append(_scope_of(document), event_id, "artifact.stopped", actor, payload)
        try:
            self.labs.destroy(str(lab["lab_instance_id"]))
        except LabRuntimeError:
            pass
        return self.get(artifact_id)

    def check(
        self,
        specs: Mapping[str, JsonObject],
        artifact_id: str,
        check_id: str,
        params: JsonObject,
        *,
        event_id: str,
        user_id: str | None = None,
        ws_id: str | None = None,
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Run one of the spec's ``allowed_checks`` against the lab (``artifact.checked``)."""
        expect = {"artifact_id": artifact_id, "check_id": check_id}
        replay = self._replay(ws_id, event_id, "artifact.checked", expect)
        if replay is not None:
            return replay.payload
        document = self._running(artifact_id, user_id, ws_id)
        artifact, lab_instance_id = self._lab(document, specs)
        if check_id not in artifact.allowed_checks:
            raise _invalid(f"check {check_id!r} is not in the spec's allowed_checks")
        try:
            self.adapters.check(check_id).validate_params(params)
            result = run_check(
                self.adapters, check_id, params, lab=self.labs, lab_instance_id=lab_instance_id
            )
        except (AdapterParamsError, DomainAdapterError) as exc:
            raise _invalid(str(exc)) from exc
        except LabRuntimeError as exc:
            raise _unavailable(f"check {check_id!r} could not run: {exc}") from exc
        payload = {"artifact_id": artifact_id, **result.to_dict()}
        return self._append(
            _scope_of(document), event_id, "artifact.checked", actor, payload
        ).payload

    def reap_idle(self, specs: Mapping[str, JsonObject]) -> list[str]:
        """Stop every running lab whose ws had no non-system event for its spec's
        ``idle_seconds``; a spec without it only stops on request."""
        with self.store.transaction() as tx:
            running = [d for _, d in ArtifactView.list(tx) if d["status"] == "running"]
        stopped: list[str] = []
        for document in running:
            spec = specs.get(str(document["spec_id"]))
            if spec is None:
                continue
            idle = LabArtifact.from_spec(str(document["artifact_id"]), spec).idle_seconds
            if idle is None:
                continue
            # ponytail: reads the whole ws log per artifact; keep a last-activity view if it grows.
            events = self.store.read(ws_id=str(document["ws_id"]))
            last = max((e.created_at for e in events if e.actor != "system"), default=None)
            if last is None or last < self.now() - timedelta(seconds=idle):
                artifact_id = str(document["artifact_id"])
                self.stop(artifact_id, event_id=str(uuid.uuid4()), reason="idle", actor="system")
                stopped.append(artifact_id)
        return stopped

    # --- internals ----------------------------------------------------------

    def _run_lab(self, run: Callable[[LabSpec], LabInfo], artifact: LabArtifact) -> LabInfo:
        environment = artifact.environment
        image, params = environment["image"], environment["params"]
        assert isinstance(image, Mapping) and isinstance(params, Mapping)
        try:
            provider = self.adapters.fixture(str(environment["fixture"]))
            spec = provider.build_lab_spec(
                ImageRef(repository=str(image["repository"]), digest=str(image["digest"])), params
            )
        except (DomainAdapterError, ValueError) as exc:
            raise _invalid(str(exc)) from exc
        try:
            return run(spec)
        except LabRuntimeError as exc:
            raise _unavailable(f"could not start a lab: {exc}") from exc

    @staticmethod
    def _lab_payload(info: LabInfo, artifact: LabArtifact) -> dict[str, PlainJson]:
        return {
            "lab_instance_id": info.lab_instance_id,
            "runtime_ref": info.runtime_ref,
            "image_digest": info.image_digest,
            "fixture_id": str(artifact.environment["fixture"]),
        }

    def _session_of(self, user_id: str, ws_id: str) -> str | None:
        """The ws's session, from its ``ws.created`` event; 404 if the user has no such ws."""
        for event in self.store.read(user_id=user_id, ws_id=ws_id):
            if event.type == "ws.created":
                return event.session_id
        raise _not_found(f"ws {ws_id!r} not found")

    def _replay(
        self, ws_id: str | None, event_id: str, event_type: str, expect: Mapping[str, str]
    ) -> StoredEventV2 | None:
        """The stored event for a resent ``event_id`` (idempotency), or ``None``."""
        if ws_id is None:
            return None
        # ponytail: scans the ws log; the store has no read-by-id yet.
        for event in self.store.read(ws_id=ws_id):
            if event.id == event_id:
                if event.type != event_type or any(
                    event.payload.get(k) != v for k, v in expect.items()
                ):
                    raise _key_reused(event_id)
                return event
        return None

    def _append(
        self,
        scope: tuple[str, str | None, str],
        event_id: str,
        event_type: str,
        actor: ActorV2,
        payload: JsonObject,
    ) -> StoredEventV2:
        user_id, session_id, ws_id = scope
        event = EventV2(
            id=event_id,
            type=event_type,
            actor=actor,
            user_id=user_id,
            session_id=session_id,
            ws_id=ws_id,
            payload=to_plain_object(payload),
        )
        try:
            with self.store.transaction() as tx:
                result = tx.append(event)
                if result.created:
                    dispatch(result.event, tx)
        except EventIdConflictError as exc:
            raise _key_reused(event_id) from exc
        return result.event


def _scope_of(document: JsonObject) -> tuple[str, str | None, str]:
    session_id = to_plain_json(document["session_id"])
    return (
        str(document["user_id"]),
        None if session_id is None else str(session_id),
        str(document["ws_id"]),
    )
