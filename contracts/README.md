# contracts/

Language-neutral boundary contracts for the harness. These are the source of
truth for wire formats; no code is generated from them. Real payloads are
checked against these schemas in contract tests (see
`harness/testing/contracts.py` and `tests/contracts/`).

## Layout

- `openapi/` — OpenAPI documents describing the HTTP API.
- `schemas/events/` — JSON Schema for append-only event payloads.
- `schemas/ws/` — JSON Schema for WebSocket messages.
- `schemas/pack/` — JSON Schema for subject pack content (definitions,
  templates, layout specs, etc.).
- `schemas/llm/` — JSON Schema for structured LLM outputs (learner-model
  updates, evaluation results, generated content candidates).

## Conventions

- JSON Schema draft 2020-12 (`"$schema": "https://json-schema.org/draft/2020-12/schema"`).
- One schema per file.
- Every schema declares a `$id` so it can be referenced from other schemas
  in the same contracts directory via local `$ref`.
- No type generation from these schemas. Application types are written and
  maintained by hand; contract tests validate real instances against these
  schemas to keep the two in sync.

## `$id` convention

`$id` = `https://morphloop.dev/contracts/` + the file path relative to
`contracts/` (e.g. `https://morphloop.dev/contracts/schemas/common/ids.json`).
Cross-file refs always use the absolute `$id`, with a `#/$defs/<name>` fragment
for files that hold several definitions. A test enforces the convention for
`schemas/common/` and `schemas/events/`.

## `schemas/common/`

Shared definitions referenced by other schemas:

- `ids.json` — identifier formats. Runtime instance ids are
  `<prefix>_<1-64 alphanumerics>` with an opaque tail: `evt_` event, `usr_`
  learner, `ses_` session, `att_` attempt, `lab_` lab instance, `evl_`
  evaluation, `ev_` evidence, `hl_` highlight, `thr_` thread, `msg_` message,
  `term_` terminal. Definition ids, skill ids, pack ids and adapter item ids
  are lowercase dotted/dashed names.
- `content-hash.json` — `sha256:<64 lowercase hex>` (pack content, Definition
  identity, OCI image digests).
- `timestamp.json` — RFC 3339 UTC with a `Z` suffix.
- `versions.json` — `version_label` (human-facing version) and
  `name_at_version` (`name@version` registry implementation refs).
- `provenance.json` — `session` (constant for a session; carried by
  `session.started`), `llm`, `lab` and `definition_ref` (carried by the event
  where they occur).
- `skill-state.json` — per-skill learner state recorded by
  `learner_skill.updated` and re-applied by rebuild.

## `schemas/events/`

Self-describing events: the envelope carries `event_type` and
`event_version`; the payload schema is
`schemas/events/payloads/<event_type>/<event_version>.json`.

- `envelope/fields.json` — shared envelope fields. Open; never validate
  against it directly.
- `envelope/append.json` — what a producer hands to the event store. Closed.
  `position` and `recorded_at` must be absent: they are DB-assigned. `event_id`,
  `occurred_at` and `idempotency_key` are producer-supplied.
- `envelope/stored.json` — an event as read back: append request plus
  `position` and `recorded_at`. Closed.
- `dispatch.json` — referenced by both envelopes: maps
  `(event_type, event_version)` to its payload schema and applies per-type
  envelope rules (fixed `actor`; `attempt_id` required for attempt-scoped
  types; `idempotency_key` required for learner and terminal events). Unknown
  types and versions are rejected.
- `payloads/<event_type>/<major>.json` — one closed payload schema per type
  and version.

Envelope notes: `attempt_id` and `activity_definition_id` are both set or both
null (null outside an activity). Pack identity and other session-constant
provenance live only on `session.started`; other events reach them through
`session_id`. Full LLM prompts are system logs and never appear in events.

Versioning: a released payload schema file is never edited. Any change,
including adding an optional field, is a new major file (`2.json`) plus a
branch in `dispatch.json`; readers upcast older versions. Stored events are
never rewritten.

Example instances live in `tests/contracts/fixtures/events/` and are checked by
`tests/contracts/test_event_contracts.py`.
