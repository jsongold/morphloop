# Event Schema and Persistence

## Goal
Persist the full learning experience as an append-only timeline.

The event log must support:
- session reconstruction,
- audit of learner-model changes,
- analytics,
- future model improvements,
- AI context retrieval,
- debugging incorrect evaluations.

## Base event envelope

```json
{
  "event_id": "evt_...",
  "position": 10452,
  "event_type": "terminal.command",
  "event_version": 1,
  "occurred_at": "2026-09-21T10:00:00Z",
  "recorded_at": "2026-09-21T10:00:00Z",
  "idempotency_key": "...",
  "causation_id": "evt_...",
  "correlation_id": "...",
  "learner_id": "usr_...",
  "session_id": "ses_...",
  "attempt_id": "att_...",
  "activity_definition_id": "diagnose-dns-resolver-failure-v1",
  "skill_ids": ["network.dns.resolution"],
  "actor": "learner",
  "payload": {},
  "metadata": {
    "pack_id": "software-engineering",
    "pack_version": "0.1.0",
    "pack_content_hash": "..."
  }
}
```

Field notes:
- `position` (ADR-0008): monotonically increasing value assigned by the database. It is the only source of event order.
- `occurred_at` / `recorded_at` (ADR-0008): `occurred_at` is the time at the source of the event; `recorded_at` is the time the database recorded it. Neither is used for ordering.
- `idempotency_key` (ADR-0008): carried by events sent by the client and the terminal bridge.
- `causation_id` (ADR-0008): the event that directly caused this event.
- `correlation_id` (ADR-0008): groups related events, such as one attempt or one chat round trip.
- `attempt_id` + `activity_definition_id` (ADR-0007): `attempt_id` is the runtime `ActivityAttempt`; `activity_definition_id` is the pack-internal id of the `ActivityDefinition` it was started from. The fully qualified definition is `pack_id` + `pack_version` + definition id. These replace the former single activity id.
- `metadata.pack_content_hash` (ADR-0010): pack content hash; see Provenance.

## Ordering, atomicity and idempotency

Defined by ADR-0008. Learning state (`LearnerSkillState`, session resume state, highlights, chat) is a projection derived from events. The MVP uses a single PostgreSQL database with synchronous projections; it is neither a full event-sourcing/CQRS framework nor a mutable database with an audit log.

- Ordering: the timeline and rebuilds are ordered by `position`. `occurred_at` is never used for ordering. Order within a session is `session_id` + `position`.
- Known caveat: with concurrent transactions, the order in which positions are assigned may differ from commit order. This is not a problem in the MVP because projections are updated synchronously inside the same transaction and no asynchronous consumer follows `position`. Revisit when an asynchronous consumer is introduced.
- Atomicity: appending an event and updating the corresponding projection happen in the same database transaction. A state where only one of them succeeded must not exist.
- Idempotency: resending an event with the same `idempotency_key` does not create a new event; the existing event is returned.
- Concurrency: learner-skill updates for the same learner are serialized. The mechanism is decided at implementation time.
- Append-only enforcement: UPDATE and DELETE on `learning_events` are forbidden at the database level, not only by application convention (AC-F1). The mechanism is decided at implementation time.
- Rebuild: the harness provides a way to discard projections and rebuild them from events. Tests verify that the rebuilt state matches the live state.
- Schema evolution: stored events are never rewritten. `event_version` identifies the stored shape, and older versions are upcast at read time.

## Provenance

Defined by ADR-0010. `pack_version` alone is not enough to reproduce or compare results. Events record:

- harness version
- `pack_id`, `pack_version`, pack content hash
- `name@version` of the registry implementations in use (learner model / policy / assessment)
- domain adapter version (ADR-0009)
- lab image digest and fixture id (at lab start)
- evaluator rubric id and version
- for events that used an LLM: provider, model, prompt version, generation parameters

`pack_version` is a human-facing label. The pack content hash, computed from the contents of the pack directory, is the source of truth for pack identity; an edit that forgot to bump the version is still detected by the hash.

Principle: record everything once at session start, and record anything that can change on the event where it occurs.

Undecided (ADR-0010): which provenance item is carried by which event, and whether `eval/` is included in the pack content hash.

## Event families

### Content/navigation
- `content.opened`
- `content.closed`
- `concept.clicked`
- `content.highlighted`
- `highlight.removed`
- `sidepane.opened`
- `visualization.opened`
- `visualization.played`
- `visualization.step_selected`
- `reality_view.opened`
- `reference.opened`

### AI
- `assistant.message_requested`
- `assistant.message_generated`
- `assistant.highlight_referenced`
- `assistant.hint_requested`
- `assistant.hint_generated`

### Terminal/runtime
- `lab.started`
- `lab.reset`
- `lab.stopped`
- `terminal.command`
- `terminal.output`
- `terminal.exit`

### Editor/browser/database
- `editor.file_opened`
- `editor.changed`
- `browser.request`
- `browser.response`
- `database.query`
- `database.result`

### Assessment/activity
- `assessment.started`
- `assessment.completed`
- `activity.started`
- `activity.submitted`
- `activity.completed`
- `activity.failed`
- `evaluation.completed`
- `evidence.created`
- `learner_skill.updated`

### Session
- `session.started`
- `session.resumed`
- `session.ended`

## Terminal event example
```json
{
  "event_type": "terminal.command",
  "payload": {
    "command": "dig api.internal",
    "cwd": "/workspace",
    "terminal_id": "term_1",
    "sequence": 18
  }
}
```

`sequence` is a payload value giving display order within one terminal. The global order of events is decided by `position` (ADR-0008).

Command output may be stored separately/chunked if large but must remain referenceable: each `terminal.output` chunk is an event (ADR-0008).

## Highlight model

A highlight must survive reload and content updates when possible.

```json
{
  "highlight_id": "hl_123",
  "content_id": "concept.network.dns",
  "content_version": "0.1.0",
  "selected_text": "recursive resolver",
  "start_offset": 148,
  "end_offset": 166,
  "semantic_anchor": "mechanism.steps[2].label",
  "context_before": "The OS forwards the query to a ",
  "context_after": " which may query authoritative servers.",
  "created_at": "..."
}
```

Prefer semantic anchors over raw DOM paths.

## AI quote/reference model
An AI user message can contain:
```json
{
  "text": "Why is this necessary?",
  "references": [
    {
      "type": "highlight",
      "id": "hl_123"
    }
  ]
}
```

The rendered UI should visibly quote the selected source.

## Learner-model update event
```json
{
  "event_type": "learner_skill.updated",
  "payload": {
    "skill_id": "network.dns.resolution",
    "previous": {"mastery_probability": 0.54},
    "next": {"mastery_probability": 0.68},
    "evidence_ids": ["ev_88", "ev_89"],
    "model_version": "bkt-lite-v1"
  }
}
```

The chain `evaluation.completed` → `evidence.created` → `learner_skill.updated` can be followed through `causation_id`: each event points to the event that directly caused it. This satisfies the learner update audit (AC-F3, ADR-0008).

## Storage rule
Never mutate historical learning events.
Corrections are new events.
This rule is enforced at the database level (ADR-0008); see Ordering, atomicity and idempotency.

PII/secrets from terminal output require redaction hooks before persistence. The hook specification is undecided (ADR-0008).
