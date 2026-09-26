"""Start, reset, stop, check and attach to lab artifacts (#62, #95).

Uses only the SDK Ports (:class:`LabRuntime`, :class:`TerminalBridge`) and the
domain adapter registry; never an adapter. Every change is one v2 event appended
with its view update in one transaction (ADR-0008). The ws is an opaque id.

The request-scoped methods (``get`` / ``start`` / ``reset`` / ``stop`` / ``check``)
take the request's own :class:`EventTransactionV2`: replay checks, view reads and
the append all happen on that one connection, and the route resolves the ws
through the ``ws`` view on it first (#103). A resent event id returns the stored
result instead of acting twice -- only for the same learner and ws: a stored
event with another owner, type or subject is a reused key (409), never a
replay, so one learner's key can not read another's artifact (#105 review).
The store is kept for the two callers without a request transaction: the
terminal recording and the idle reaper.

A lab lives until an explicit stop or, when its spec declares ``idle_seconds``,
until its ws has had no non-system event for that long
(:meth:`LabArtifactService.reap_idle`); the runtime's ``lifetime_seconds`` stays
the backstop.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from harness.sdk import (
    ActorV2,
    AdapterParamsError,
    DomainAdapterError,
    DomainAdapterRegistry,
    EventIdConflictError,
    EventStoreV2,
    EventTransactionV2,
    EventV2,
    ImageRef,
    JsonObject,
    LabInfo,
    LabRuntime,
    LabRuntimeError,
    LabSpec,
    PlainJson,
    StoredEventV2,
    TerminalBridge,
    TerminalOpenRequest,
    TerminalSize,
    TerminalTool,
    dispatch,
    run_check,
    to_plain_json,
    to_plain_object,
)
from swe.artifacts.lab.lab import ArtifactView, LabArtifact
from swe.artifacts.lab.terminal import LabTerminal


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


type Scope = tuple[str, str | None, str]
"""``(user_id, session_id, ws_id)`` of the events an artifact records."""


@dataclass(slots=True, kw_only=True)
class LabArtifactService:
    """The runtime parts; the pack's artifact specs (``pack/v2/artifact-spec.json``
    by id) are passed per call, so one service serves whichever pack is wired."""

    store: EventStoreV2
    labs: LabRuntime
    terminals: TerminalBridge
    adapters: DomainAdapterRegistry
    now: Callable[[], datetime] = field(default=_utc_now)
    new_id: Callable[[str], str] = field(default=_new_id)

    # --- reads --------------------------------------------------------------

    def get(
        self,
        tx: EventTransactionV2,
        artifact_id: str,
        *,
        user_id: str | None = None,
        ws_id: str | None = None,
    ) -> JsonObject:
        """The ``artifact`` view document; 404 if unknown or not the user's / the ws's."""
        document = ArtifactView.get(tx, artifact_id)
        if (
            document is None
            or (user_id is not None and document["user_id"] != user_id)
            or (ws_id is not None and document["ws_id"] != ws_id)
        ):
            raise _not_found(f"artifact {artifact_id!r} not found")
        return document

    def list_artifacts(
        self,
        tx: EventTransactionV2,
        *,
        user_id: str,
        ws_id: str,
        spec_id: str | None = None,
    ) -> list[JsonObject]:
        """The learner's artifacts in ``ws_id``, in creation order; type-neutral."""
        return ArtifactView.list_for_ws(tx, ws_id, user_id=user_id, spec_id=spec_id)

    def _running(
        self, tx: EventTransactionV2, artifact_id: str, user_id: str | None, ws_id: str | None
    ) -> JsonObject:
        document = self.get(tx, artifact_id, user_id=user_id, ws_id=ws_id)
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
        tx: EventTransactionV2,
        specs: Mapping[str, JsonObject],
        *,
        user_id: str,
        session_id: str | None,
        ws_id: str,
        spec_id: str,
        event_id: str,
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Start a lab from ``spec_id`` in ``ws_id`` (``artifact.started``).

        ``session_id`` is the ws's session, resolved by the caller (``ws_or_404``).
        The lab is destroyed again if its event cannot be appended.
        """
        scope: Scope = (user_id, session_id, ws_id)
        owner = (user_id, ws_id)
        replay = self._replay(tx, event_id, "artifact.started", owner, {"spec_id": spec_id})
        if replay is not None:
            return self.get(tx, str(replay.payload["artifact_id"]), user_id=user_id, ws_id=ws_id)
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
            self._append(tx, scope, event_id, "artifact.started", actor, payload)
        except Exception:
            self.labs.destroy(lab_instance_id)
            raise
        return self.get(tx, artifact_id)

    def reset(
        self,
        tx: EventTransactionV2,
        specs: Mapping[str, JsonObject],
        artifact_id: str,
        *,
        event_id: str,
        user_id: str,
        ws_id: str,
    ) -> JsonObject:
        """Replace the lab with a fresh one from the same spec (``artifact.reset``)."""
        expect = {"artifact_id": artifact_id}
        if self._replay(tx, event_id, "artifact.reset", (user_id, ws_id), expect):
            return self.get(tx, artifact_id, user_id=user_id, ws_id=ws_id)
        document = self._running(tx, artifact_id, user_id, ws_id)
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
        self._append(tx, _scope_of(document), event_id, "artifact.reset", "learner", payload)
        return self.get(tx, artifact_id, user_id=user_id, ws_id=ws_id)

    def stop(
        self,
        tx: EventTransactionV2,
        artifact_id: str,
        *,
        event_id: str,
        user_id: str,
        ws_id: str,
        reason: str = "requested",
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Record ``artifact.stopped``, then destroy the lab (best effort; lifetime backstops)."""
        expect = {"artifact_id": artifact_id, "reason": reason}
        if self._replay(tx, event_id, "artifact.stopped", (user_id, ws_id), expect):
            return self.get(tx, artifact_id, user_id=user_id, ws_id=ws_id)
        document = self._running(tx, artifact_id, user_id, ws_id)
        lab = document["lab"]
        assert isinstance(lab, Mapping)
        payload = {"artifact_id": artifact_id, "reason": reason}
        self._append(tx, _scope_of(document), event_id, "artifact.stopped", actor, payload)
        try:
            self.labs.destroy(str(lab["lab_instance_id"]))
        except LabRuntimeError:
            pass
        return self.get(tx, artifact_id, user_id=user_id, ws_id=ws_id)

    def check(
        self,
        tx: EventTransactionV2,
        specs: Mapping[str, JsonObject],
        artifact_id: str,
        check_id: str,
        params: JsonObject | None,
        *,
        event_id: str,
        user_id: str,
        ws_id: str,
        actor: ActorV2 = "learner",
    ) -> JsonObject:
        """Run one of the spec's ``allowed_checks`` against the lab (``artifact.checked``).

        ``params=None`` (the GUI's ``check`` call, since ``learner_view`` hides the
        spec it would otherwise read them from): the spec's own target params for
        ``check_id`` (``spec.checks``, #124), so the recorded ``artifact.checked``
        matches the target exactly and a drill answered from it can be judged
        (#149). Passed explicitly, even ``{}``, params are used as given.
        """
        expect = {"artifact_id": artifact_id, "check_id": check_id}
        replay = self._replay(tx, event_id, "artifact.checked", (user_id, ws_id), expect)
        if replay is not None:
            return replay.payload
        document = self._running(tx, artifact_id, user_id, ws_id)
        artifact, lab_instance_id = self._lab(document, specs)
        if check_id not in artifact.allowed_checks:
            raise _invalid(f"check {check_id!r} is not in the spec's allowed_checks")
        if params is None:
            params = artifact.target_params(check_id) or {}
        try:
            self.adapters.check(check_id).validate_params(params)
            result = run_check(
                self.adapters, check_id, params, lab=self.labs, lab_instance_id=lab_instance_id
            )
        except (AdapterParamsError, DomainAdapterError) as exc:
            raise _invalid(str(exc)) from exc
        except LabRuntimeError as exc:
            raise _unavailable(f"check {check_id!r} could not run: {exc}") from exc
        payload = {
            "artifact_id": artifact_id,
            **result.to_dict(),
            "params": to_plain_object(params),
        }
        return self._append(
            tx, _scope_of(document), event_id, "artifact.checked", actor, payload
        ).payload

    def reap_idle(self, specs: Mapping[str, JsonObject]) -> list[str]:
        """Stop every running lab whose ws had no non-system event for its spec's
        ``idle_seconds``; a spec without it only stops on request.

        Runs outside any request, so it opens its own transactions. Safe to run
        from several API workers at once: the idle stop's event id is derived
        from the artifact id (an artifact is idle-stopped at most once), so a
        second worker's stop is a replay or a state conflict, both skipped.
        """
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
                event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{artifact_id}:idle-stop"))
                try:
                    with self.store.transaction() as tx:
                        self.stop(
                            tx,
                            artifact_id,
                            event_id=event_id,
                            user_id=str(document["user_id"]),
                            ws_id=str(document["ws_id"]),
                            reason="idle",
                            actor="system",
                        )
                except ArtifactError:
                    continue  # another worker stopped it first
                stopped.append(artifact_id)
        return stopped

    # --- terminal -----------------------------------------------------------

    async def open_terminal(
        self, artifact_id: str, size: TerminalSize, *, user_id: str | None = None
    ) -> LabTerminal:
        """Open a PTY in the running lab; its traffic is recorded as artifact.input/output,
        each chunk in a transaction of its own (a WebSocket has no request transaction)."""
        with self.store.transaction() as tx:
            document = self._running(tx, artifact_id, user_id, None)
        lab = document["lab"]
        assert isinstance(lab, Mapping)
        lab_instance_id = str(lab["lab_instance_id"])
        tool = self._tool(str(lab["fixture_id"]))
        launch = tool.launch()
        try:
            session = await self.terminals.open(
                TerminalOpenRequest(
                    lab_instance_id=lab_instance_id,
                    runtime_ref=str(lab["runtime_ref"]),
                    terminal_id=self.new_id("term"),
                    argv=launch.argv,
                    size=size,
                    env=launch.env,
                    workdir=launch.workdir,
                )
            )
        except Exception as exc:  # adapter failures are lab failures to the learner
            raise _unavailable(f"could not open a terminal: {exc}") from exc
        scope = _scope_of(document)

        def record(
            event_id: str, event_type: str, actor: ActorV2, payload: JsonObject
        ) -> StoredEventV2:
            with self.store.transaction() as tx:
                return self._append(tx, scope, event_id, event_type, actor, payload)

        return LabTerminal(
            artifact_id=artifact_id,
            lab_instance_id=lab_instance_id,
            session=session,
            detector=tool.new_command_detector(),
            append=record,
        )

    # --- internals ----------------------------------------------------------

    def _tool(self, fixture: str) -> TerminalTool:
        # ponytail: the spec names no tool, so the fixture's adapter must have exactly one.
        split = self.adapters.split_item_id(fixture)
        tools = self.adapters.adapter(split[0]).tools if split else {}
        if len(tools) != 1:
            raise _invalid(f"adapter of {fixture!r} must register exactly one terminal tool")
        return next(iter(tools.values()))

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

    @staticmethod
    def _replay(
        tx: EventTransactionV2,
        event_id: str,
        event_type: str,
        owner: tuple[str, str],
        expect: Mapping[str, str],
    ) -> StoredEventV2 | None:
        """The stored event for a resent ``event_id`` (idempotency), or ``None``.

        A resend is the same learner and ws (``owner`` = ``(user_id, ws_id)``),
        event type and subject (``expect``); anything else stored
        under the id is a reused key (409). The full candidate can not be
        compared here (``replay_or_conflict``): the payload holds what acting
        produces (a new artifact or lab id, a check's observation).
        """
        event = tx.get(event_id)
        if event is None:
            return None
        if (
            (event.user_id, event.ws_id) != owner
            or event.type != event_type
            or any(event.payload.get(k) != v for k, v in expect.items())
        ):
            raise _key_reused(event_id)
        return event

    @staticmethod
    def _append(
        tx: EventTransactionV2,
        scope: Scope,
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
            result = tx.append(event)
        except EventIdConflictError as exc:
            raise _key_reused(event_id) from exc
        if result.created:
            dispatch(result.event, tx)
        return result.event


def _scope_of(document: JsonObject) -> Scope:
    session_id = to_plain_json(document["session_id"])
    return (
        str(document["user_id"]),
        None if session_id is None else str(session_id),
        str(document["ws_id"]),
    )
