# `/v2` route conventions

Rules every resource route (`harness/api/v2/routes/<resource>.py`) follows.
They exist so each route does not re-discover the same input bugs (#87, #92).

## Request bodies

- Subclass `V2Model` (`harness/api/v2/models.py`), never `BaseModel` directly.
  Unknown fields are rejected with 422, matching `additionalProperties: false`.
- Every free-text `str` field is `Text`. It rejects NUL (`\u0000`), which
  Postgres jsonb cannot store (the in-memory store would not catch it).
- Mirror the OpenAPI schema's constraints on the field, e.g.
  `Annotated[Text, Field(min_length=1, max_length=2000)]` or `pattern=...`.
  The request model is the only check before the event store.
- A POST body never carries `user_id`; use `UserIdDep`.

## Shared dependencies (`harness/api/v2/deps.py`)

- `EventIdDep`: the new event's `id` from the `Idempotency-Key` header
  (UUID, else 400; generated when absent). Use it as the event `id`.
- `EventTransactionV2Dep`, `PackV2Dep`, `GeneratedDocumentsDep`, `UserIdDep`.
  Do not add route-local env vars or loaders.

## Errors

- The same `Idempotency-Key` with the same body replays (no new event).
  With a different body the store raises `EventIdConflictError`, which
  `harness/api/problems.py` renders as `409 state-conflict`. Do not catch it.

## Regression test per route

For each POST, add a test that sends an out-of-schema body (unknown field,
NUL, over-long text) and asserts a 4xx problem **and** that no event was
stored. See `tests/api/v2/test_models.py` for the pattern.
