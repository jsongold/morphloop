"""The v0.1 learning loop application services (ADR-0008, ADR-0012, ADR-0013).

:class:`LearningLoop` sits between the HTTP/WebSocket layer and the Ports: it
owns every event the harness writes, the projections they update, the LLM calls
and the pack reads. The API layer routes, serializes and maps
:class:`~harness.core.loop.errors.LoopError` onto problem responses; it holds no
learning logic.

The v0.1 chain (``docs/ACCEPTANCE_CRITERIA.md``, "v0.1 is complete only when"):
start a session on an imported pack version, start an attempt (which starts a
disposable lab), run terminal commands, open content, highlight it, ask the
tutor, submit, evaluate, update the learner model, read the timeline and resume.

Concurrency: learner-skill updates take :meth:`EventTransaction.lock_learner`
before the previous state is read and hold it until the update is appended
(ADR-0008). Every other write is a single short transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from harness.core.contract_schemas import ContractSchemas
from harness.core.domain_adapter import (
    CheckResult,
    DomainAdapterError,
    DomainAdapterRegistry,
    TerminalTool,
    run_check,
)
from harness.core.learner_model.resolve import load_learner_model
from harness.core.learner_model.types import (
    EvidenceRecord,
    LearnerModel,
    LearnerModelInput,
    LearnerModelOutputError,
    LearnerSkillUpdate,
    Signal,
    SkillState,
)
from harness.core.loop.appender import EventAppender, EventDraft
from harness.core.loop.context import (
    MissionContext,
    assert_no_reference_solution,
    build_evaluator_context,
    build_memo_context,
    build_tutor_context,
    tutor_reference_ids,
)
from harness.core.loop.errors import (
    InvalidRequestError,
    LabUnavailableError,
    LLMFailedError,
    LoopError,
    NotFoundError,
    StateConflictError,
    ValidationFailedError,
    problem_body,
)
from harness.core.loop.ids import Clock, IdGenerator, utc_now, uuid_ids
from harness.core.loop.llm_roles import LLMEvaluator, LLMMemoSummarizer, LLMTutor
from harness.core.loop.options import (
    EvaluatorOptions,
    MemoSummarizerOptions,
    PackOptionError,
    TutorOptions,
    evaluator_options,
    memo_summarizer_options,
    registry_implementations,
    tutor_options,
)
from harness.core.loop.projections import (
    ATTEMPT_PROJECTION,
    CHAT_PROJECTION,
    HIGHLIGHT_PROJECTION,
    IDEMPOTENCY_PROJECTION,
    LAB_PROJECTION,
    LEARNER_SESSION_PROJECTION,
    LEARNER_SKILL_PROJECTION,
    MEMO_PROJECTION,
    SESSION_PROJECTION,
    highlight_key,
    learner_skill_key,
    memo_key,
    rebuild_projections,
)
from harness.core.loop.redaction import NullRedaction, RedactionHook
from harness.core.loop.terminal import TerminalConnection, TerminalContext
from harness.core.loop.views import (
    CONTENT_KINDS,
    ActivityView,
    AttemptResult,
    AttemptState,
    ChatExchange,
    ContentDocument,
    ContentSummary,
    LabState,
    MemoView,
    PackView,
    SessionState,
    SessionView,
    SkillStateView,
    TimelinePage,
    UiState,
    activity_view,
    content_document,
    content_summary,
    skill_ids_of_activity,
)
from harness.core.pack.catalog import PackCatalog, PackNotFoundError
from harness.core.pack.model import (
    Definition,
    LoadedPack,
    PackRef,
    ReferenceSolution,
    learner_view_of_activity,
)
from harness.core.ports import (
    Actor,
    EventStore,
    EventTransaction,
    ImageRef,
    JsonObject,
    JsonValue,
    LabRuntime,
    LabRuntimeError,
    LLMProvider,
    PlainJson,
    StoredEvent,
    TerminalBridge,
    TerminalOpenRequest,
    TerminalSize,
    format_timestamp,
    to_plain_object,
)
from harness.core.registry.algorithms import AlgorithmRegistry

CLIENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "content.opened",
        "content.highlighted",
        "visualization.step_selected",
        "memo.edited",
    }
)
TIMELINE_DEFAULT_LIMIT = 100
TIMELINE_MAX_LIMIT = 500


@dataclass(slots=True, kw_only=True)
class _LabHandle:
    """In-process terminal state of a lab; ``runtime_ref`` lives in ``loop_lab``."""

    lab_instance_id: str
    attempt_id: str
    status: str
    terminal_id: str | None = None


def _string(document: Mapping[str, PlainJson], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str):
        raise NotFoundError(f"projection field {name!r} is missing")
    return value


def _optional_string(document: Mapping[str, PlainJson], name: str) -> str | None:
    value = document.get(name)
    return value if isinstance(value, str) else None


def _integer(document: Mapping[str, PlainJson], name: str) -> int:
    value = document.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotFoundError(f"projection field {name!r} is missing")
    return value


def _string_list(document: Mapping[str, PlainJson], name: str) -> list[str]:
    value = document.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _object(value: PlainJson | None, name: str) -> dict[str, PlainJson]:
    if not isinstance(value, dict):
        raise NotFoundError(f"projection field {name!r} is missing")
    return value


logger = logging.getLogger(__name__)


class LearningLoop:
    """The application services of the v0.1 loop. Synchronous except the terminal."""

    def __init__(
        self,
        *,
        store: EventStore,
        schemas: ContractSchemas,
        adapters: DomainAdapterRegistry,
        algorithms: AlgorithmRegistry,
        llm: LLMProvider,
        labs: LabRuntime,
        terminals: TerminalBridge,
        harness_version: str,
        catalog: PackCatalog | None = None,
        ids: IdGenerator = uuid_ids,
        now: Clock = utc_now,
        redaction: RedactionHook | None = None,
    ) -> None:
        self._store = store
        self._schemas = schemas
        self._adapters = adapters
        self._algorithms = algorithms
        self._llm = llm
        self._labs = labs
        self._terminals = terminals
        self._harness_version = harness_version
        self._catalog = catalog if catalog is not None else PackCatalog(store)
        self._ids = ids
        self._now = now
        self._appender = EventAppender(
            schemas=schemas,
            ids=ids,
            redaction=redaction if redaction is not None else NullRedaction(),
        )
        self._handles: dict[str, _LabHandle] = {}

    # --- packs -------------------------------------------------------------

    def list_packs(self, pack_id: str | None = None) -> list[PackView]:
        """Imported pack versions, newest import order not tracked (see README notes)."""
        return [
            PackView(pack=ref, title=self._catalog.get_pack(ref).title)
            for ref in self._catalog.list_packs(pack_id)
        ]

    # --- sessions ----------------------------------------------------------

    def start_session(
        self,
        *,
        learner_id: str,
        pack: PackRef,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> SessionState:
        """Append ``session.started`` with session-constant provenance (ADR-0010)."""
        replay = self._replay_of(idempotency_key, "session.started")
        if replay is not None:
            return self.session_state(_string(replay, "session_id"))
        loaded = self._require_pack(pack)
        try:
            adapter_versions = self._adapters.adapter_versions(loaded.domain_adapters)
        except KeyError as exc:
            raise InvalidRequestError(f"domain adapter {exc} is not registered") from exc
        provenance: dict[str, JsonValue] = {
            "harness_version": self._harness_version,
            "pack": {
                "pack_id": pack.pack_id,
                "pack_version": pack.pack_version,
                "pack_content_hash": pack.content_hash,
            },
            "registry_implementations": registry_implementations(loaded.registry),
            "domain_adapters": dict(adapter_versions),
        }
        session_id = self._ids("ses")
        draft = EventDraft(
            event_type="session.started",
            event_version=1,
            actor="learner",
            learner_id=learner_id,
            session_id=session_id,
            attempt_id=None,
            activity_definition_id=None,
            payload={"provenance": provenance},
            occurred_at=self._at(occurred_at),
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=session_id,
        )
        with self._store.transaction() as tx:
            result = self._appender.append(tx, draft)
        return self.session_state(result.event.session_id)

    def session_state(self, session_id: str) -> SessionState:
        """Everything needed to restore the screen after a reload (AC-F4)."""
        document = self._session_document(session_id)
        attempts = _string_list(document, "attempt_ids")
        active_id = _optional_string(document, "active_attempt_id")
        completed: AttemptState | None = None
        for attempt_id in reversed(attempts):
            state = self.attempt_state(attempt_id)
            if state.status == "completed":
                completed = state
                break
        ui_state = _object(document.get("ui_state"), "ui_state")
        steps = _object(ui_state.get("visualization_steps"), "ui_state.visualization_steps")
        open_content = ui_state.get("open_content")
        return SessionState(
            session=self._session_view(document),
            last_position=_integer(document, "last_position"),
            active_attempt=None if active_id is None else self.attempt_state(active_id),
            ui_state=UiState(
                open_content=open_content if isinstance(open_content, dict) else None,
                visualization_steps=[
                    value for _, value in sorted(steps.items()) if isinstance(value, dict)
                ],
            ),
            last_completed_attempt=completed,
        )

    def learner_sessions(self, learner_id: str) -> list[SessionView]:
        """A learner's sessions, most recently started first (AC-F4)."""
        with self._store.transaction() as tx:
            rows = tx.list_projection(LEARNER_SESSION_PROJECTION, key_prefix=learner_id + "/")
        views: list[SessionView] = []
        for _, row in reversed(list(rows)):
            session_id = _string(to_plain_object(row), "session_id")
            views.append(self._session_view(self._session_document(session_id)))
        return views

    def learner_skills(self, learner_id: str, pack_id: str | None = None) -> list[SkillStateView]:
        """The learner-skill projection, rebuilt from ``learner_skill.updated`` (AC-F6)."""
        prefix = f"{learner_id}/" if pack_id is None else f"{learner_id}/{pack_id}/"
        with self._store.transaction() as tx:
            rows = tx.list_projection(LEARNER_SKILL_PROJECTION, key_prefix=prefix)
        return [self._skill_view(to_plain_object(row)) for _, row in rows]

    def session_layout(self, session_id: str) -> JsonObject | None:
        """The pack's layout document, opaque to the harness (ADR-0003)."""
        pack = self._pack_of_session(session_id)
        layouts = self._catalog.list_definitions(pack, "layout")
        return layouts[0].document if layouts else None

    def session_activities(self, session_id: str) -> list[ActivityView]:
        """Whitelist views of the activities the learner may start (AC-J6)."""
        pack = self._pack_of_session(session_id)
        return [
            activity_view(definition)
            for definition in self._catalog.list_definitions(pack, "activity")
        ]

    def session_content(self, session_id: str, kind: str | None = None) -> list[ContentSummary]:
        """Learner-facing pack documents; every other kind has no route (AC-J6)."""
        pack = self._pack_of_session(session_id)
        kinds = CONTENT_KINDS if kind is None else (self._content_kind(kind),)
        summaries: list[ContentSummary] = []
        for content_kind in kinds:
            summaries.extend(
                content_summary(definition, pack)
                for definition in self._catalog.list_definitions(pack, content_kind)
            )
        return summaries

    def session_content_document(
        self, session_id: str, kind: str, definition_id: str
    ) -> ContentDocument:
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, self._content_kind(kind), definition_id)
        return content_document(definition, pack)

    def session_timeline(
        self, session_id: str, *, after_position: int = 0, limit: int = TIMELINE_DEFAULT_LIMIT
    ) -> TimelinePage:
        """One page of the session timeline, ordered by ``position`` (AC-F2, AC-F7)."""
        if limit < 1 or limit > TIMELINE_MAX_LIMIT:
            raise InvalidRequestError(f"limit must be between 1 and {TIMELINE_MAX_LIMIT}")
        self._session_document(session_id)
        events = list(
            self._store.read_session(session_id, after_position=after_position, limit=limit + 1)
        )
        has_more = len(events) > limit
        page = events[:limit]
        return TimelinePage(
            events=page,
            last_position=page[-1].position if page else after_position,
            has_more=has_more,
        )

    def session_highlights(self, session_id: str) -> list[StoredEvent]:
        """Stored ``content.highlighted`` events, in position order (AC-D2)."""
        self._session_document(session_id)
        with self._store.transaction() as tx:
            rows = tx.list_projection(HIGHLIGHT_PROJECTION, key_prefix=session_id + "/")
        documents = [to_plain_object(row) for _, row in rows]
        documents.sort(key=lambda row: _integer(row, "position"))
        return [self._stored_event(_object(row.get("event"), "event")) for row in documents]

    def session_chat(self, session_id: str, thread_id: str | None = None) -> list[StoredEvent]:
        """Stored chat events of the session, in position order (AC-F4).

        ``thread_id`` filters to one popup thread; an unknown or empty thread
        returns no events.
        """
        self._session_document(session_id)
        with self._store.transaction() as tx:
            rows = tx.list_projection(CHAT_PROJECTION, key_prefix=session_id + "/")
        events = [
            self._stored_event(_object(to_plain_object(row).get("event"), "event"))
            for _, row in rows
        ]
        if thread_id is None:
            return events
        return [
            event
            for event in events
            if _string(to_plain_object(event.payload), "thread_id") == thread_id
        ]

    def session_memos(self, session_id: str) -> list[MemoView]:
        """Current learning memos of the session, by first ``memo.recorded`` position."""
        self._session_document(session_id)
        with self._store.transaction() as tx:
            rows = tx.list_projection(MEMO_PROJECTION, key_prefix=session_id + "/")
        documents = [to_plain_object(row) for _, row in rows]
        documents.sort(key=lambda row: _integer(row, "first_recorded_position"))
        return [_memo_view(document) for document in documents]

    # --- client events -----------------------------------------------------

    def append_client_event(
        self,
        *,
        session_id: str,
        event_type: str,
        event_version: int,
        payload: JsonObject,
        attempt_id: str | None,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> StoredEvent:
        """Append one learner UI event (highlight, content opened, step selected)."""
        if event_type not in CLIENT_EVENT_TYPES:
            raise InvalidRequestError(f"{event_type!r} is not a client event type")
        replay = self._replay_of(idempotency_key, event_type)
        if replay is not None:
            return self._event_at(_string(replay, "session_id"), _integer(replay, "position"))
        session = self._session_document(session_id)
        definition_id: str | None = None
        if attempt_id is not None:
            attempt = self._attempt_document(attempt_id)
            if _string(attempt, "session_id") != session_id:
                raise InvalidRequestError(f"attempt {attempt_id!r} is not in this session")
            definition_id = _string(attempt, "activity_definition_id")
        if event_type == "content.highlighted":
            self._check_highlight_unused(session_id, payload)
        if event_type == "memo.edited":
            self._check_memo_exists(session_id, payload)
        draft = EventDraft(
            event_type=event_type,
            event_version=event_version,
            actor="learner",
            learner_id=_string(session, "learner_id"),
            session_id=session_id,
            attempt_id=attempt_id,
            activity_definition_id=definition_id,
            payload=payload,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=attempt_id if attempt_id is not None else session_id,
        )
        with self._store.transaction() as tx:
            return self._appender.append(tx, draft).event

    # --- attempts and labs -------------------------------------------------

    def start_attempt(
        self,
        *,
        session_id: str,
        activity_definition_id: str,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> AttemptState:
        """Start an attempt and, for a lab-backed activity, its LabInstance."""
        replay = self._replay_of(idempotency_key, "activity.started")
        if replay is not None:
            return self.attempt_state(_string(replay, "attempt_id"))
        session = self._session_document(session_id)
        active = _optional_string(session, "active_attempt_id")
        if active is not None:
            raise StateConflictError(f"attempt {active!r} of this session is not completed")
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, "activity", activity_definition_id)
        attempt_id = self._ids("att")
        draft = EventDraft(
            event_type="activity.started",
            event_version=1,
            actor="learner",
            learner_id=_string(session, "learner_id"),
            session_id=session_id,
            attempt_id=attempt_id,
            activity_definition_id=activity_definition_id,
            payload={"activity_definition_hash": definition.document_hash},
            occurred_at=self._at(occurred_at),
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=attempt_id,
        )
        with self._store.transaction() as tx:
            result = self._appender.append(tx, draft)
        if not result.created:
            return self.attempt_state(self._require_attempt_id(result.event))
        if "environment" in definition.document:
            self._start_lab(
                attempt_id=result.event.attempt_id or attempt_id,
                definition=definition,
                trigger="initial",
                replaces=None,
                causation_id=result.event.event_id,
            )
        return self.attempt_state(attempt_id)

    def attempt_state(self, attempt_id: str) -> AttemptState:
        """Attempt status, its current lab and, once completed, the evaluation result."""
        document = self._attempt_document(attempt_id)
        session_id = _string(document, "session_id")
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, "activity", _string(document, "activity_definition_id"))
        status = _string(document, "status")
        error = document.get("last_submission_error")
        lab_instance_id = _optional_string(document, "lab_instance_id")
        return AttemptState(
            attempt_id=attempt_id,
            session_id=session_id,
            activity=activity_view(definition),
            status=status,
            started_at=_string(document, "started_at"),
            lab=None if lab_instance_id is None else self.lab_state(lab_instance_id),
            last_submission_error=error if isinstance(error, dict) else None,
            result=self._attempt_result(document),
        )

    def lab_state(self, lab_instance_id: str) -> LabState:
        document = self._lab_document(lab_instance_id)
        replaced_by = _optional_string(document, "replaced_by_lab_instance_id")
        stopped = _optional_string(document, "stopped_event_id")
        handle = self._handles.get(lab_instance_id)
        if replaced_by is not None or stopped is not None:
            status = "stopped"
        elif handle is not None:
            status = handle.status
        else:
            status = "ready" if self._labs.status(lab_instance_id) == "running" else "stopped"
        return LabState(
            lab_instance_id=lab_instance_id,
            attempt_id=_string(document, "attempt_id"),
            status=status,
            terminal_id=None if handle is None else handle.terminal_id,
            replaced_by_lab_instance_id=replaced_by,
        )

    def reset_lab(
        self,
        *,
        lab_instance_id: str,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> LabState:
        """Reset the lab to its fixture; a reset creates a new LabInstance (AC-B4)."""
        if self._replay_of(idempotency_key, "lab.reset") is not None:
            replacement = _optional_string(
                self._lab_document(lab_instance_id), "replaced_by_lab_instance_id"
            )
            return self.lab_state(replacement if replacement is not None else lab_instance_id)
        document = self._lab_document(lab_instance_id)
        if _optional_string(document, "replaced_by_lab_instance_id") is not None:
            raise StateConflictError(f"lab {lab_instance_id!r} was already replaced")
        attempt_id = _string(document, "attempt_id")
        attempt = self._attempt_document(attempt_id)
        if _string(attempt, "status") != "active":
            raise StateConflictError(f"attempt {attempt_id!r} is not active")
        session_id = _string(attempt, "session_id")
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, "activity", _string(attempt, "activity_definition_id"))
        draft = EventDraft(
            event_type="lab.reset",
            event_version=1,
            actor="learner",
            learner_id=_string(attempt, "learner_id"),
            session_id=session_id,
            attempt_id=attempt_id,
            activity_definition_id=_string(attempt, "activity_definition_id"),
            payload={"lab_instance_id": lab_instance_id},
            occurred_at=self._at(occurred_at),
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=attempt_id,
        )
        with self._store.transaction() as tx:
            result = self._appender.append(tx, draft)
        if not result.created:
            replacement = _optional_string(
                self._lab_document(lab_instance_id), "replaced_by_lab_instance_id"
            )
            return self.lab_state(replacement if replacement is not None else lab_instance_id)
        return self._start_lab(
            attempt_id=attempt_id,
            definition=definition,
            trigger="reset",
            replaces=lab_instance_id,
            causation_id=result.event.event_id,
        )

    def submit_attempt(
        self,
        *,
        attempt_id: str,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> AttemptState:
        """Append ``activity.submitted``; :meth:`evaluate_attempt` runs the chain."""
        if self._replay_of(idempotency_key, "activity.submitted") is not None:
            return self.attempt_state(attempt_id)
        document = self._attempt_document(attempt_id)
        if self.attempt_state(attempt_id).status != "active":
            raise StateConflictError(f"attempt {attempt_id!r} is not active")
        lab_instance_id = _optional_string(document, "lab_instance_id")
        if lab_instance_id is None:
            raise InvalidRequestError(
                f"attempt {attempt_id!r} has no lab instance to submit (activity.submitted v1 "
                "requires one)"
            )
        draft = EventDraft(
            event_type="activity.submitted",
            event_version=1,
            actor="learner",
            learner_id=_string(document, "learner_id"),
            session_id=_string(document, "session_id"),
            attempt_id=attempt_id,
            activity_definition_id=_string(document, "activity_definition_id"),
            payload={"lab_instance_id": lab_instance_id},
            occurred_at=self._at(occurred_at),
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=attempt_id,
        )
        with self._store.transaction() as tx:
            self._appender.append(tx, draft)
        return self.attempt_state(attempt_id)

    def evaluate_attempt(self, attempt_id: str) -> AttemptState:
        """Run the evaluation chain of a submitted attempt (AC-A3, AC-E4, AC-E5).

        Deterministic checks first, then the LLM evaluator, then one learner-model
        update per skill with evidence. ``evaluation.completed`` →
        ``evidence.created`` → ``learner_skill.updated`` → ``activity.completed``
        are appended in one transaction, so a failure anywhere leaves no partial
        state (AC-E4). The failure itself is appended as ``evaluation.failed``,
        which returns the attempt to ``active`` with the error attached, so a
        restarted process still sees it and accepts a resubmission.
        """
        try:
            return self._evaluate(attempt_id)
        except LoopError as error:
            self._record_evaluation_failure(attempt_id, error)
            raise

    # --- chat --------------------------------------------------------------

    def send_chat_message(
        self,
        *,
        session_id: str,
        text: str,
        references: Sequence[JsonObject],
        attempt_id: str | None = None,
        thread_id: str | None = None,
        requested_mode: str | None = None,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> ChatExchange:
        """Record the question, call the tutor and record the reply (AC-D3, AC-D5, AC-E1)."""
        session = self._session_document(session_id)
        pack = self._pack_of_session(session_id)
        options = self._tutor_options(pack)
        replay = self._replay_of(idempotency_key, "assistant.message_requested")
        mission = self._mission_context(session, attempt_id)
        mode = self._resolve_mode(options, requested_mode, mission)
        highlights = self._referenced_highlights(session_id, references)
        thread = thread_id if thread_id is not None else self._ids("thr")
        message_id = self._ids("msg")
        definition_id = None if mission is None else mission.activity_definition_id
        request_draft = EventDraft(
            event_type="assistant.message_requested",
            event_version=1,
            actor="learner",
            learner_id=_string(session, "learner_id"),
            session_id=session_id,
            attempt_id=None if mission is None else mission.attempt_id,
            activity_definition_id=definition_id,
            payload={
                "message_id": message_id,
                "thread_id": thread,
                "text": text,
                "requested_mode": requested_mode,
                "references": [to_plain_object(reference) for reference in references],
            },
            occurred_at=self._at(occurred_at),
            idempotency_key=idempotency_key,
            causation_id=None,
            correlation_id=thread,
        )
        if replay is not None:
            request = self._event_at(_string(replay, "session_id"), _integer(replay, "position"))
        else:
            with self._store.transaction() as tx:
                request = self._appender.append(tx, request_draft).event
        existing = self._reply_to(session_id, request.event_id)
        if existing is not None:
            return ChatExchange(
                request=request,
                reply=existing,
                memo=self._memo_for_thread(session_id, pack, request),
            )
        request_payload = to_plain_object(request.payload)
        reply = self._generate_reply(
            session=session,
            pack=pack,
            options=options,
            mission=mission,
            mode=mode,
            request=request,
            highlights=highlights,
            question={
                "message_id": _string(request_payload, "message_id"),
                "thread_id": _string(request_payload, "thread_id"),
                "text": _string(request_payload, "text"),
                "requested_mode": requested_mode,
                "references": [to_plain_object(reference) for reference in references],
            },
        )
        memo = self._memo_after_reply(session_id, pack, mission, request, reply)
        return ChatExchange(request=request, reply=reply, memo=memo)

    # --- terminal ----------------------------------------------------------

    async def open_terminal(
        self, *, lab_instance_id: str, size: TerminalSize
    ) -> TerminalConnection:
        """Open a PTY in a running lab and record its traffic (AC-B1, AC-B2)."""
        document = self._lab_document(lab_instance_id)
        runtime_ref = _optional_string(document, "runtime_ref")
        if runtime_ref is None or _optional_string(document, "replaced_by_lab_instance_id"):
            raise LabUnavailableError(f"lab {lab_instance_id!r} has no runtime handle to attach to")
        attempt_id = _string(document, "attempt_id")
        attempt = self._attempt_document(attempt_id)
        session_id = _string(attempt, "session_id")
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, "activity", _string(attempt, "activity_definition_id"))
        tool = self._terminal_tool(definition)
        launch = tool.launch()
        terminal_id = self._ids("term")
        try:
            session = await self._terminals.open(
                TerminalOpenRequest(
                    lab_instance_id=lab_instance_id,
                    runtime_ref=runtime_ref,
                    terminal_id=terminal_id,
                    argv=launch.argv,
                    size=size,
                    env=launch.env,
                    workdir=launch.workdir,
                )
            )
        except Exception as exc:  # adapter failures are lab failures to the learner
            raise LabUnavailableError(f"could not open a terminal: {exc}") from exc
        self._handles[lab_instance_id] = _LabHandle(
            lab_instance_id=lab_instance_id,
            attempt_id=attempt_id,
            status="ready",
            terminal_id=terminal_id,
        )
        return TerminalConnection(
            session=session,
            context=TerminalContext(
                terminal_id=terminal_id,
                lab_instance_id=lab_instance_id,
                learner_id=_string(attempt, "learner_id"),
                session_id=session_id,
                attempt_id=attempt_id,
                activity_definition_id=_string(attempt, "activity_definition_id"),
            ),
            detector=tool.new_command_detector(),
            store=self._store,
            appender=self._appender,
            now=self._now,
        )

    # --- maintenance -------------------------------------------------------

    def rebuild(self) -> int:
        """Drop the loop projections and replay the log through the same appliers (AC-F6)."""
        return rebuild_projections(self._store)

    # --- internals: reads --------------------------------------------------

    def _at(self, occurred_at: datetime | None) -> datetime:
        return occurred_at if occurred_at is not None else self._now()

    def _projection(self, projection: str, key: str, what: str) -> dict[str, PlainJson]:
        with self._store.transaction() as tx:
            document = tx.get_projection(projection, key)
        if document is None:
            raise NotFoundError(f"no {what} {key!r}")
        return to_plain_object(document)

    def _replay_of(self, idempotency_key: str, event_type: str) -> dict[str, PlainJson] | None:
        """The event a previous request with this key produced, if any (AC-F5).

        The store keys idempotency itself, but its Port has no lookup by key and
        a second call would generate new ids (a new session id, a new attempt
        id), so the resend is recognised here before a second envelope is built.
        """
        with self._store.transaction() as tx:
            document = tx.get_projection(IDEMPOTENCY_PROJECTION, idempotency_key)
        if document is None:
            return None
        found = to_plain_object(document)
        stored_type = _string(found, "event_type")
        if stored_type != event_type:
            raise StateConflictError(
                f"idempotency_key {idempotency_key!r} was used for a {stored_type} event"
            )
        return found

    def _event_at(self, session_id: str, position: int) -> StoredEvent:
        events = self._store.read_session(session_id, after_position=position - 1, limit=1)
        if not events or events[0].position != position:
            raise NotFoundError(f"no event at position {position} of session {session_id!r}")
        return events[0]

    def _session_document(self, session_id: str) -> dict[str, PlainJson]:
        return self._projection(SESSION_PROJECTION, session_id, "session")

    def _attempt_document(self, attempt_id: str) -> dict[str, PlainJson]:
        return self._projection(ATTEMPT_PROJECTION, attempt_id, "attempt")

    def _lab_document(self, lab_instance_id: str) -> dict[str, PlainJson]:
        return self._projection(LAB_PROJECTION, lab_instance_id, "lab instance")

    def _session_view(self, document: Mapping[str, PlainJson]) -> SessionView:
        return SessionView(
            session_id=_string(document, "session_id"),
            learner_id=_string(document, "learner_id"),
            pack=self._pack_ref(document),
            started_at=_string(document, "started_at"),
            session_started_event_id=_string(document, "session_started_event_id"),
        )

    def _skill_view(self, document: Mapping[str, PlainJson]) -> SkillStateView:
        return SkillStateView(
            pack_id=_string(document, "pack_id"),
            skill_id=_string(document, "skill_id"),
            state=_object(document.get("state"), "state"),
            update_count=_integer(document, "update_count"),
            last_update_event_id=_string(document, "last_update_event_id"),
            updated_at=_string(document, "updated_at"),
        )

    def _pack_ref(self, document: Mapping[str, PlainJson]) -> PackRef:
        pack = _object(document.get("pack"), "pack")
        return PackRef(
            pack_id=_string(pack, "pack_id"),
            pack_version=_string(pack, "pack_version"),
            content_hash=_string(pack, "pack_content_hash"),
        )

    def _pack_of_session(self, session_id: str) -> PackRef:
        return self._pack_ref(self._session_document(session_id))

    def _require_pack(self, pack: PackRef) -> LoadedPack:
        try:
            return self._catalog.get_pack(pack)
        except PackNotFoundError as exc:
            raise NotFoundError(f"pack {pack.key!r} is not imported") from exc

    def _definition(self, pack: PackRef, kind: str, definition_id: str) -> Definition:
        try:
            return self._catalog.get_definition(pack, kind, definition_id)
        except PackNotFoundError as exc:
            raise NotFoundError(f"no {kind} {definition_id!r} in pack {pack.key!r}") from exc

    def _content_kind(self, kind: str) -> str:
        if kind not in CONTENT_KINDS:
            raise NotFoundError(f"{kind!r} is not a learner-facing content kind")
        return kind

    def _stored_event(self, document: Mapping[str, PlainJson]) -> StoredEvent:
        return stored_event_from_dict(document)

    def _require_attempt_id(self, event: StoredEvent) -> str:
        if event.attempt_id is None:
            raise StateConflictError(f"event {event.event_id} carries no attempt")
        return event.attempt_id

    def _check_highlight_unused(self, session_id: str, payload: JsonObject) -> None:
        highlight_id = payload.get("highlight_id")
        if not isinstance(highlight_id, str):
            raise ValidationFailedError("content.highlighted needs a highlight_id")
        with self._store.transaction() as tx:
            existing = tx.get_projection(
                HIGHLIGHT_PROJECTION, highlight_key(session_id, highlight_id)
            )
        if existing is not None:
            raise StateConflictError(f"highlight {highlight_id!r} already exists")

    def _check_memo_exists(self, session_id: str, payload: JsonObject) -> None:
        memo_id = payload.get("memo_id")
        if not isinstance(memo_id, str):
            raise ValidationFailedError("memo.edited needs a memo_id")
        with self._store.transaction() as tx:
            existing = tx.get_projection(MEMO_PROJECTION, memo_key(session_id, memo_id))
        if existing is None:
            raise InvalidRequestError(f"memo {memo_id!r} does not exist; nothing to edit")

    def _attempt_result(self, document: Mapping[str, PlainJson]) -> AttemptResult | None:
        outcome = _optional_string(document, "outcome")
        completion_id = _optional_string(document, "completion_event_id")
        evaluation_id = _optional_string(document, "evaluation_event_id")
        position = document.get("evaluation_position")
        if outcome is None or completion_id is None or evaluation_id is None:
            return None
        if isinstance(position, bool) or not isinstance(position, int):
            return None
        session_id = _string(document, "session_id")
        events = {
            event.event_id: event
            for event in self._store.read_session(session_id, after_position=position - 1)
        }
        evidence_ids = _string_list(document, "evidence_event_ids")
        update_ids = _string_list(document, "skill_update_event_ids")
        try:
            return AttemptResult(
                outcome=outcome,
                evaluation=events[evaluation_id],
                evidence=[events[event_id] for event_id in evidence_ids],
                skill_updates=[events[event_id] for event_id in update_ids],
                completion=events[completion_id],
            )
        except KeyError as exc:
            raise NotFoundError(f"event {exc} of the evaluation chain is missing") from exc

    # --- internals: labs ---------------------------------------------------

    def _start_lab(
        self,
        *,
        attempt_id: str,
        definition: Definition,
        trigger: str,
        replaces: str | None,
        causation_id: str,
    ) -> LabState:
        attempt = self._attempt_document(attempt_id)
        session_id = _string(attempt, "session_id")
        pack = self._pack_of_session(session_id)
        environment_id = definition.document.get("environment")
        if not isinstance(environment_id, str):
            raise InvalidRequestError(f"activity {definition.key!r} has no environment")
        environment = self._definition(pack, "environment", environment_id)
        document = environment.document
        fixture_id = document.get("fixture")
        image = document.get("image")
        params = document.get("params")
        if not isinstance(fixture_id, str) or not isinstance(image, Mapping):
            raise InvalidRequestError(f"environment {environment_id!r} is malformed")
        try:
            provider = self._adapters.fixture(fixture_id)
        except DomainAdapterError as exc:
            raise InvalidRequestError(str(exc)) from exc
        image_ref = ImageRef(repository=str(image["repository"]), digest=str(image["digest"]))
        spec = provider.build_lab_spec(image_ref, params if isinstance(params, Mapping) else {})
        lab_instance_id = self._ids("lab")
        try:
            if replaces is None:
                info = self._labs.start(lab_instance_id, spec)
            else:
                info = self._labs.reset(replaces, new_lab_instance_id=lab_instance_id, spec=spec)
        except LabRuntimeError as exc:
            raise LabUnavailableError(f"could not start a lab: {exc}") from exc
        self._handles[lab_instance_id] = _LabHandle(
            lab_instance_id=lab_instance_id,
            attempt_id=attempt_id,
            status="ready",
        )
        if replaces is not None:
            self._handles.pop(replaces, None)
        draft = EventDraft(
            event_type="lab.started",
            event_version=2,
            actor="system",
            learner_id=_string(attempt, "learner_id"),
            session_id=session_id,
            attempt_id=attempt_id,
            activity_definition_id=_string(attempt, "activity_definition_id"),
            payload={
                "lab_instance_id": lab_instance_id,
                "environment": {
                    "definition_id": environment.key,
                    "definition_hash": environment.document_hash,
                },
                "trigger": trigger,
                "replaces_lab_instance_id": replaces,
                "runtime_ref": info.runtime_ref,
                "provenance": {
                    "image_digest": info.image_digest,
                    "fixture_id": fixture_id,
                },
            },
            occurred_at=self._now(),
            idempotency_key=None,
            causation_id=causation_id,
            correlation_id=attempt_id,
        )
        try:
            with self._store.transaction() as tx:
                self._appender.append(tx, draft)
        except Exception:
            self._labs.destroy(lab_instance_id)
            self._handles.pop(lab_instance_id, None)
            raise
        return self.lab_state(lab_instance_id)

    def _stop_lab(self, *, attempt_id: str, lab_instance_id: str, causation_id: str) -> None:
        """Record ``lab.stopped`` for the attempt and tear its lab runtime down.

        The event is authoritative; the runtime destroy is best-effort so a
        teardown failure never fails an evaluation that already completed (the
        lifetime timer and ``reap_expired`` remain as backstops).
        """
        attempt = self._attempt_document(attempt_id)
        draft = EventDraft(
            event_type="lab.stopped",
            event_version=1,
            actor="system",
            learner_id=_string(attempt, "learner_id"),
            session_id=_string(attempt, "session_id"),
            attempt_id=attempt_id,
            activity_definition_id=_string(attempt, "activity_definition_id"),
            payload={"lab_instance_id": lab_instance_id, "reason": "attempt_completed"},
            occurred_at=self._now(),
            idempotency_key=None,
            causation_id=causation_id,
            correlation_id=attempt_id,
        )
        with self._store.transaction() as tx:
            self._appender.append(tx, draft)
        try:
            self._labs.destroy(lab_instance_id)
        except LabRuntimeError:
            logger.exception(
                "failed to destroy lab %r after attempt %r", lab_instance_id, attempt_id
            )
        finally:
            self._handles.pop(lab_instance_id, None)

    def _terminal_tool(self, definition: Definition) -> TerminalTool:
        tools = definition.document.get("tools")
        if not isinstance(tools, Sequence) or isinstance(tools, str) or not tools:
            raise InvalidRequestError(f"activity {definition.key!r} declares no tool")
        try:
            return self._adapters.tool(str(tools[0]))
        except DomainAdapterError as exc:
            raise InvalidRequestError(str(exc)) from exc

    # --- internals: evaluation --------------------------------------------

    def _evaluate(self, attempt_id: str) -> AttemptState:
        document = self._attempt_document(attempt_id)
        if _string(document, "status") != "evaluating":
            raise StateConflictError(f"attempt {attempt_id!r} was not submitted")
        session_id = _string(document, "session_id")
        learner_id = _string(document, "learner_id")
        definition_id = _string(document, "activity_definition_id")
        pack = self._pack_of_session(session_id)
        definition = self._definition(pack, "activity", definition_id)
        options = self._evaluator_options(pack)
        lab_instance_id = _optional_string(document, "lab_instance_id")
        checks = self._run_checks(definition, lab_instance_id)
        events = [
            event
            for event in self._store.read_session(session_id)
            if event.attempt_id == attempt_id
        ]
        evaluator_id = definition.document.get("evaluator")
        if not isinstance(evaluator_id, str):
            raise InvalidRequestError(f"activity {definition_id!r} declares no evaluator")
        evaluator = self._definition(pack, "evaluator", evaluator_id)
        skill_ids = skill_ids_of_activity(definition.document)
        skills = [self._definition(pack, "skill", skill_id) for skill_id in skill_ids]
        solution = (
            self._reference_solution(pack, definition_id)
            if options.include_reference_solution
            else None
        )
        context = build_evaluator_context(
            pack_id=pack.pack_id,
            attempt_id=attempt_id,
            activity=definition.document,
            evaluator=evaluator.document,
            skill_definitions=[skill.document for skill in skills],
            checks=checks,
            events=events,
            reference_solution=solution,
        )
        judgment = self._evaluator(pack, options).judge(
            context,
            skills_under_test=skill_ids,
            dimensions=self._dimensions(evaluator),
            event_ids=[event.event_id for event in events],
        )
        evaluation_id = self._ids("evl")
        evidence_ids = [self._ids("ev") for _ in judgment.evidence]
        learner_model = load_learner_model(
            catalog=self._catalog,
            pack=pack,
            registry=self._algorithms,
            llm=self._llm,
            schemas=self._schemas,
        )
        occurred_at = self._now()
        with self._store.transaction() as tx:
            tx.lock_learner(learner_id)
            evaluation_event = self._appender.append(
                tx,
                self._attempt_draft(
                    document,
                    "evaluation.completed",
                    "system",
                    {
                        "evaluation_id": evaluation_id,
                        "evaluator": {
                            "definition_id": evaluator.key,
                            "definition_hash": evaluator.document_hash,
                        },
                        "lab_instance_id": lab_instance_id,
                        "checks": [check.to_dict() for check in checks],
                        "success": judgment.success,
                        "rationale": judgment.rationale,
                        "provenance": judgment.provenance.to_dict(),
                    },
                    occurred_at,
                    causation_id=None,
                ),
            ).event
            records: dict[str, list[EvidenceRecord]] = {}
            last_evidence_event: dict[str, str] = {}
            for evidence_id, item in zip(evidence_ids, judgment.evidence, strict=True):
                event = self._appender.append(
                    tx,
                    self._attempt_draft(
                        document,
                        "evidence.created",
                        "system",
                        item.payload(evidence_id=evidence_id, evaluation_id=evaluation_id),
                        occurred_at,
                        causation_id=evaluation_event.event_id,
                    ),
                ).event
                records.setdefault(item.skill_id, []).append(
                    EvidenceRecord(
                        evidence_id=evidence_id,
                        evaluation_id=evaluation_id,
                        skill_id=item.skill_id,
                        signal=_signal(item.signal),
                        strength=item.strength,
                        dimension=item.dimension,
                        rationale=item.rationale,
                        supporting_event_ids=item.supporting_event_ids,
                    )
                )
                last_evidence_event[item.skill_id] = event.event_id
            for skill_id in sorted(records):
                update = self._update_skill(
                    tx,
                    learner_model=learner_model,
                    pack=pack,
                    learner_id=learner_id,
                    skill_id=skill_id,
                    evidence=records[skill_id],
                )
                self._appender.append(
                    tx,
                    self._attempt_draft(
                        document,
                        "learner_skill.updated",
                        "system",
                        update.to_payload(),
                        occurred_at,
                        causation_id=last_evidence_event[skill_id],
                    ),
                )
            completed_event = self._appender.append(
                tx,
                self._attempt_draft(
                    document,
                    "activity.completed",
                    "system",
                    {
                        "outcome": "passed" if judgment.success else "failed",
                        "evaluation_id": evaluation_id,
                    },
                    occurred_at,
                    causation_id=evaluation_event.event_id,
                ),
            ).event
        if lab_instance_id is not None:
            self._stop_lab(
                attempt_id=attempt_id,
                lab_instance_id=lab_instance_id,
                causation_id=completed_event.event_id,
            )
        return self.attempt_state(attempt_id)

    def _record_evaluation_failure(self, attempt_id: str, error: LoopError) -> None:
        """Append ``evaluation.failed`` for the pending submission, if there is one."""
        try:
            document = self._attempt_document(attempt_id)
        except NotFoundError:
            return
        submissions = _string_list(document, "submission_event_ids")
        if _string(document, "status") != "evaluating" or not submissions:
            return  # e.g. "was not submitted": nothing pending to fail
        draft = self._attempt_draft(
            document,
            "evaluation.failed",
            "system",
            {"submission_event_id": submissions[-1], "problem": problem_body(error)},
            self._now(),
            causation_id=submissions[-1],
        )
        # One failure per submission, even if two evaluations of it race.
        draft = replace(draft, idempotency_key=f"evaluation.failed:{submissions[-1]}")
        with self._store.transaction() as tx:
            self._appender.append(tx, draft)

    def _attempt_draft(
        self,
        attempt: Mapping[str, PlainJson],
        event_type: str,
        actor: Actor,
        payload: JsonObject,
        occurred_at: datetime,
        *,
        causation_id: str | None,
    ) -> EventDraft:
        return EventDraft(
            event_type=event_type,
            event_version=1,
            actor=actor,
            learner_id=_string(attempt, "learner_id"),
            session_id=_string(attempt, "session_id"),
            attempt_id=_string(attempt, "attempt_id"),
            activity_definition_id=_string(attempt, "activity_definition_id"),
            payload=payload,
            occurred_at=occurred_at,
            idempotency_key=None,
            causation_id=causation_id,
            correlation_id=_string(attempt, "attempt_id"),
        )

    def _update_skill(
        self,
        tx: EventTransaction,
        *,
        learner_model: LearnerModel,
        pack: PackRef,
        learner_id: str,
        skill_id: str,
        evidence: Sequence[EvidenceRecord],
    ) -> LearnerSkillUpdate:
        previous_document = tx.get_projection(
            LEARNER_SKILL_PROJECTION, learner_skill_key(learner_id, pack.pack_id, skill_id)
        )
        previous: SkillState | None = None
        if previous_document is not None:
            state = to_plain_object(previous_document).get("state")
            if isinstance(state, dict):
                previous = SkillState.from_dict(state)
        skill = self._definition(pack, "skill", skill_id)
        request = LearnerModelInput(
            pack_id=pack.pack_id,
            skill_id=skill_id,
            skill_definition=skill.document,
            previous=previous,
            evidence=list(evidence),
        )
        try:
            update = learner_model.update(request)
        except LearnerModelOutputError as exc:
            raise LLMFailedError(
                f"learner-model output for {skill_id!r} failed validation: {exc}",
                errors=exc.errors,
            ) from exc
        except Exception as exc:
            raise LLMFailedError(f"learner-model call for {skill_id!r} failed: {exc}") from exc
        return update

    def _run_checks(self, definition: Definition, lab_instance_id: str | None) -> list[CheckResult]:
        checks = definition.document.get("checks")
        if not isinstance(checks, Sequence) or isinstance(checks, str):
            return []
        if lab_instance_id is None:
            raise InvalidRequestError(f"activity {definition.key!r} needs a lab to run its checks")
        results: list[CheckResult] = []
        for entry in checks:
            if not isinstance(entry, Mapping):
                continue
            check_id = entry.get("check")
            params = entry.get("params")
            if not isinstance(check_id, str):
                continue
            try:
                results.append(
                    run_check(
                        self._adapters,
                        check_id,
                        params if isinstance(params, Mapping) else {},
                        lab=self._labs,
                        lab_instance_id=lab_instance_id,
                    )
                )
            except DomainAdapterError as exc:
                raise InvalidRequestError(str(exc)) from exc
            except LabRuntimeError as exc:
                raise LabUnavailableError(f"check {check_id!r} could not run: {exc}") from exc
        return results

    def _dimensions(self, evaluator: Definition) -> list[str]:
        semantic = evaluator.document.get("semantic")
        if not isinstance(semantic, Mapping):
            return []
        dimensions = semantic.get("dimensions")
        if not isinstance(dimensions, Sequence) or isinstance(dimensions, str):
            return []
        return [
            str(item["id"])
            for item in dimensions
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]

    def _reference_solution(self, pack: PackRef, activity_id: str) -> ReferenceSolution | None:
        try:
            return self._catalog.get_reference_solution(pack, activity_id)
        except PackNotFoundError:
            return None

    # --- internals: chat ---------------------------------------------------

    def _mission_context(
        self, session: Mapping[str, PlainJson], attempt_id: str | None
    ) -> MissionContext | None:
        chosen = (
            attempt_id if attempt_id is not None else _optional_string(session, "active_attempt_id")
        )
        if chosen is None:
            return None
        attempt = self._attempt_document(chosen)
        if _string(attempt, "session_id") != _string(session, "session_id"):
            raise InvalidRequestError(f"attempt {chosen!r} is not in this session")
        pack = self._pack_ref(session)
        definition = self._definition(pack, "activity", _string(attempt, "activity_definition_id"))
        status = _string(attempt, "status")
        return MissionContext(
            attempt_id=chosen,
            activity_definition_id=definition.key,
            activity_definition_hash=definition.document_hash,
            status=status,
            learner_view=learner_view_of_activity(definition.document),
        )

    def _resolve_mode(
        self, options: TutorOptions, requested: str | None, mission: MissionContext | None
    ) -> str:
        try:
            return options.resolve_mode(
                requested, attempt_unfinished=mission is not None and mission.unfinished
            )
        except PackOptionError as exc:
            raise InvalidRequestError(str(exc)) from exc

    def _referenced_highlights(
        self, session_id: str, references: Sequence[JsonObject]
    ) -> list[JsonObject]:
        highlights: list[JsonObject] = []
        for reference in references:
            if reference.get("type") != "highlight":
                continue
            highlight_id = reference.get("id")
            if not isinstance(highlight_id, str):
                raise ValidationFailedError("a highlight reference needs an id")
            document = self._projection(
                HIGHLIGHT_PROJECTION,
                highlight_key(session_id, highlight_id),
                "highlight",
            )
            event = self._stored_event(_object(document.get("event"), "event"))
            highlights.append(to_plain_object(event.payload))
        return highlights

    def _reply_to(self, session_id: str, request_event_id: str) -> StoredEvent | None:
        for event in self.session_chat(session_id):
            if (
                event.event_type == "assistant.message_generated"
                and event.causation_id == request_event_id
            ):
                return event
        return None

    # --- internals: memo summarization ------------------------------------

    def _memo_ids_of(self, thread_id: str) -> tuple[str, str, str] | None:
        """``(highlight_id, memo_id, tail)`` of a highlight thread, or ``None``."""
        if not thread_id.startswith("thr_"):
            return None
        tail = thread_id[4:]
        return f"hl_{tail}", f"memo_{tail}", tail

    def _memo_for_thread(
        self, session_id: str, pack: PackRef, request: StoredEvent
    ) -> MemoView | None:
        """The memo already recorded for ``request``'s thread, if any (replay path)."""
        thread_id = _string(to_plain_object(request.payload), "thread_id")
        derived = self._memo_ids_of(thread_id)
        if derived is None or self._memo_summarizer_options(pack) is None:
            return None
        _, memo_id, _ = derived
        with self._store.transaction() as tx:
            document = tx.get_projection(MEMO_PROJECTION, memo_key(session_id, memo_id))
        if document is None:
            return None
        return _memo_view(to_plain_object(document))

    def _memo_after_reply(
        self,
        session_id: str,
        pack: PackRef,
        mission: MissionContext | None,
        request: StoredEvent,
        reply: StoredEvent,
    ) -> MemoView | None:
        """Summarize ``request``'s highlight thread and record the memo (AC-J6).

        Returns ``None`` (recording nothing) when the pack has no summarizer,
        the thread is not a highlight thread, the learner already edited the
        memo, or the summarizer fails; the reply is never affected.
        """
        try:
            options = self._memo_summarizer_options(pack)
        except (InvalidRequestError, NotFoundError):
            logger.exception("memo summarizer is unavailable; memo: null")
            return None
        if options is None:
            return None
        thread_id = _string(to_plain_object(request.payload), "thread_id")
        derived = self._memo_ids_of(thread_id)
        if derived is None:
            return None
        highlight_id, memo_id, _ = derived
        highlight = self._highlight_document(session_id, highlight_id)
        if highlight is None:
            return None
        with self._store.transaction() as tx:
            existing = tx.get_projection(MEMO_PROJECTION, memo_key(session_id, memo_id))
        if existing is not None and to_plain_object(existing).get("edited_by_learner") is True:
            return None
        thread = self.session_chat(session_id, thread_id=thread_id)
        if not thread or thread[-1].event_id != reply.event_id:
            return None
        highlight_payload = to_plain_object(highlight.payload)
        context = build_memo_context(highlight=highlight_payload, thread=thread)
        if mission is not None:
            assert_no_reference_solution(
                context, self._reference_solution(pack, mission.activity_definition_id)
            )
        try:
            note = self._memo_summarizer(pack, options).summarize(context)
        except (LLMFailedError, InvalidRequestError, NotFoundError):
            logger.exception("memo summarizer failed for thread %r; memo: null", thread_id)
            return None
        draft = EventDraft(
            event_type="memo.recorded",
            event_version=1,
            actor="system",
            learner_id=request.learner_id,
            session_id=session_id,
            attempt_id=request.attempt_id,
            activity_definition_id=request.activity_definition_id,
            payload={
                "memo_id": memo_id,
                "highlight_id": highlight_id,
                "thread_id": thread_id,
                "source_event_ids": [highlight.event_id] + [event.event_id for event in thread],
                "title": note.title,
                "body": note.body,
                "provenance": note.provenance.to_dict(),
            },
            occurred_at=self._now(),
            idempotency_key=None,
            causation_id=reply.event_id,
            correlation_id=request.correlation_id,
        )
        with self._store.transaction() as tx:
            current = tx.get_projection(MEMO_PROJECTION, memo_key(session_id, memo_id))
            if current is not None and to_plain_object(current).get("edited_by_learner") is True:
                return None
            self._appender.append(tx, draft)
        with self._store.transaction() as tx:
            document = tx.get_projection(MEMO_PROJECTION, memo_key(session_id, memo_id))
        assert document is not None
        return _memo_view(to_plain_object(document))

    def _highlight_document(self, session_id: str, highlight_id: str) -> StoredEvent | None:
        try:
            document = self._projection(
                HIGHLIGHT_PROJECTION, highlight_key(session_id, highlight_id), "highlight"
            )
        except NotFoundError:
            return None
        return self._stored_event(_object(document.get("event"), "event"))

    def _generate_reply(
        self,
        *,
        session: Mapping[str, PlainJson],
        pack: PackRef,
        options: TutorOptions,
        mission: MissionContext | None,
        mode: str,
        request: StoredEvent,
        highlights: Sequence[JsonObject],
        question: JsonObject,
    ) -> StoredEvent:
        session_id = _string(session, "session_id")
        recent = self._recent_events(session_id, options.recent_events_limit, request.event_id)
        context = build_tutor_context(
            pack_id=pack.pack_id,
            mode=mode,
            question=question,
            mission=mission,
            highlights=highlights,
            ui_state=self.session_state(session_id).ui_state,
            recent_events=recent,
            learner_skills=self.learner_skills(_string(session, "learner_id"), pack.pack_id),
        )
        if mission is not None:
            assert_no_reference_solution(
                context, self._reference_solution(pack, mission.activity_definition_id)
            )
        reply = self._tutor(pack, options).reply(
            context, allowed_reference_ids=tutor_reference_ids(highlights, recent)
        )
        payload: dict[str, JsonValue] = {
            "message_id": self._ids("msg"),
            "thread_id": _string(to_plain_object(request.payload), "thread_id"),
            "in_reply_to": _string(to_plain_object(request.payload), "message_id"),
            "text": reply.text,
            "mode": mode,
            "references": [to_plain_object(reference) for reference in reply.references],
            "provenance": reply.provenance.to_dict(),
        }
        draft = EventDraft(
            event_type="assistant.message_generated",
            event_version=1,
            actor="tutor",
            learner_id=request.learner_id,
            session_id=session_id,
            attempt_id=request.attempt_id,
            activity_definition_id=request.activity_definition_id,
            payload=payload,
            occurred_at=self._now(),
            idempotency_key=None,
            causation_id=request.event_id,
            correlation_id=request.correlation_id,
        )
        with self._store.transaction() as tx:
            return self._appender.append(tx, draft).event

    def _recent_events(self, session_id: str, limit: int, exclude: str) -> list[StoredEvent]:
        events = [
            event for event in self._store.read_session(session_id) if event.event_id != exclude
        ]
        return events[-limit:]

    # --- internals: role construction --------------------------------------

    def _evaluator_options(self, pack: PackRef) -> EvaluatorOptions:
        loaded = self._catalog.get_pack(pack)
        try:
            return evaluator_options(loaded.registry)
        except PackOptionError as exc:
            raise InvalidRequestError(str(exc)) from exc

    def _tutor_options(self, pack: PackRef) -> TutorOptions:
        loaded = self._catalog.get_pack(pack)
        try:
            return tutor_options(loaded.registry)
        except PackOptionError as exc:
            raise InvalidRequestError(str(exc)) from exc

    def _memo_summarizer_options(self, pack: PackRef) -> MemoSummarizerOptions | None:
        loaded = self._catalog.get_pack(pack)
        try:
            return memo_summarizer_options(loaded.registry)
        except PackOptionError as exc:
            raise InvalidRequestError(str(exc)) from exc

    def _prompt(
        self,
        pack: PackRef,
        options: EvaluatorOptions | TutorOptions | MemoSummarizerOptions,
    ) -> str:
        llm = options.selection.llm
        try:
            return self._catalog.get_prompt(pack, llm.prompt_id, llm.prompt_version).text
        except PackNotFoundError as exc:
            raise NotFoundError(
                f"prompt {llm.prompt_id!r} version {llm.prompt_version!r} is not in the pack"
            ) from exc

    def _evaluator(self, pack: PackRef, options: EvaluatorOptions) -> LLMEvaluator:
        return LLMEvaluator(
            options=options,
            prompt=self._prompt(pack, options),
            llm=self._llm,
            schemas=self._schemas,
        )

    def _tutor(self, pack: PackRef, options: TutorOptions) -> LLMTutor:
        return LLMTutor(
            options=options,
            prompt=self._prompt(pack, options),
            llm=self._llm,
            schemas=self._schemas,
        )

    def _memo_summarizer(self, pack: PackRef, options: MemoSummarizerOptions) -> LLMMemoSummarizer:
        return LLMMemoSummarizer(
            options=options,
            prompt=self._prompt(pack, options),
            llm=self._llm,
            schemas=self._schemas,
        )


def _signal(value: str) -> Signal:
    if value == "positive":
        return "positive"
    if value == "negative":
        return "negative"
    raise LLMFailedError(f"evidence signal {value!r} is not positive or negative")


def _memo_view(document: Mapping[str, PlainJson]) -> MemoView:
    """A :class:`MemoView` from a ``loop_memo`` projection document."""
    return MemoView(
        memo_id=_string(document, "memo_id"),
        highlight_id=_string(document, "highlight_id"),
        thread_id=_string(document, "thread_id"),
        title=_string(document, "title"),
        body=_string(document, "body"),
        source_event_ids=_string_list(document, "source_event_ids"),
        edited_by_learner=to_plain_object(document).get("edited_by_learner") is True,
        updated_at=_string(document, "updated_at"),
        last_event_id=_string(document, "last_event_id"),
    )


def stored_event_from_dict(document: Mapping[str, PlainJson]) -> StoredEvent:
    """Rebuild a :class:`StoredEvent` from the wire form stored in a projection."""
    actor = _actor(document.get("actor"))
    payload = document.get("payload")
    return StoredEvent(
        event_id=_string(document, "event_id"),
        event_type=_string(document, "event_type"),
        event_version=_integer(document, "event_version"),
        occurred_at=_timestamp(document, "occurred_at"),
        idempotency_key=_optional_string(document, "idempotency_key"),
        causation_id=_optional_string(document, "causation_id"),
        correlation_id=_optional_string(document, "correlation_id"),
        learner_id=_string(document, "learner_id"),
        session_id=_string(document, "session_id"),
        attempt_id=_optional_string(document, "attempt_id"),
        activity_definition_id=_optional_string(document, "activity_definition_id"),
        actor=actor,
        payload=payload if isinstance(payload, dict) else {},
        position=_integer(document, "position"),
        recorded_at=_timestamp(document, "recorded_at"),
    )


def _actor(value: PlainJson | None) -> Actor:
    if value == "learner":
        return "learner"
    if value == "tutor":
        return "tutor"
    if value == "system":
        return "system"
    raise NotFoundError("stored event has no actor")


def _timestamp(document: Mapping[str, PlainJson], name: str) -> datetime:
    value = _string(document, name)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_event_time(value: datetime) -> str:
    """RFC 3339 UTC, as the contracts write timestamps."""
    return format_timestamp(value)
