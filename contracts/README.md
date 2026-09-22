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

Slice 0 only establishes this skeleton and the validation helper. The actual
v0.1 schemas are added later.
