# `/v2` route conventions

Rules every resource route (`harness/api/v2/routes/<resource>.py`) follows.
They exist so each route does not re-discover the same input bugs (#87, #92).

## Request bodies and parameters

- Subclass `V2Model` (`harness/api/v2/models.py`), never `BaseModel` directly.
  Unknown fields and NaN / Infinity floats are rejected with 422.
- Every free-text `str` field is `Text`. It rejects NUL (`\u0000`) and
  unpaired surrogates, which Postgres cannot store (in-memory would not catch it).
- Mirror the OpenAPI schema's constraints on the field, e.g.
  `Annotated[Text, Field(min_length=1, max_length=2000)]` or `pattern=...`.
  The request model is the only check before the event store.
- Strict types: integers are `StrictInt` (no `"3"` or `3.0`); an optional
  field the schema does not allow as `null` is non-nullable (omitted is not
  `null`) -- add a `model_validator(mode="before")` calling `reject_null(data,
  *field_names)` (`models.py`). `Query(...)` parameters carry the same
  constraints as the contract (wrap a free-text one in `Text` too).
- A POST body never carries `user_id`; use `UserIdDep`.

## Shared dependencies (`harness/api/v2/deps.py`)

- `EventIdDep`: the new event's `id` from the `Idempotency-Key` header
  (UUID, else 400; generated when absent). Use it as the event `id`.
- `EventTransactionV2Dep`, `PackV2Dep`, `GeneratedDocumentsDep`, `UserIdDep`.
  Do not add route-local env vars or loaders.
- `ws_or_404(tx, ws_id, user_id=...)`: a ws-scoped route resolves `ws_id` to
  the ws's session/owner through the `ws` view on the request transaction.
  Never `store.read(...)` while the transaction is open (a second pooled
  connection per request), and never answer for a ws that is missing or
  belongs to another learner -- both are 404.

## Idempotency

- Check replay first: build the candidate event from the request, then call
  `replay_or_conflict(tx, candidate)` (`deps.py`) before any pack/state
  lookup or validation. A stored event with the same content is returned
  (answer from it); other content raises `EventIdConflictError`, rendered as
  `409 idempotency-key-reused` by `harness/api/problems.py`. Do not catch it.
- Anything generated into the event (ids, timestamps in the payload) must be
  derived deterministically from the event id, or a resend never matches.
- When part of the payload comes from a lookup that could change (a pack item,
  say), check `tx.get(event_id)` first: if found, build the candidate from the
  *stored* event's own fields (not a fresh lookup) so a resend still replays
  after the pack changed, instead of failing the lookup before ever reaching
  `replay_or_conflict`.

## Events and views

- Event types that belong to a session/workspace declare `x-scope` in their
  payload schema (`["session_id"]` or `["session_id", "ws_id"]`); the store
  then rejects an append without those ids.
- List order = creation order: store the event `position` in the view
  document and sort by it. Never sort by uuid keys.

## Regression test per route

For each POST, add a test that sends an out-of-schema body (unknown field,
NUL, over-long text) and asserts a 4xx problem **and** that no event was
stored. See `tests/api/v2/test_models.py` for the pattern.
