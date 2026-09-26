# contracts/schemas/search/

JSON Schema for the search request/response wire shape (issue #180): one
query across textbook blocks, drill questions and memos, resolved by a
deterministic keyword leg (Postgres `tsvector('simple')` + `pg_trgm`), a
semantic leg (`pgvector` similarity over an embedded query) or both, fused by
RRF.

## Files

- `request.json` — `{query, mode?, resources?, limit?}`. `mode` (`keyword` |
  `semantic` | `hybrid`) defaults to `hybrid` when absent — a harness
  protocol default, not a pack tuning value (ADR-0002 governs pack content,
  not this). `resources` filters by resource label (ADR-0018); the harness
  holds no closed enum of resources.
- `response.json` — `{results: [{kind, id, parent_id?, score, source}]}`. A
  result is a reference only; the caller re-fetches full content through the
  owning resource's own endpoint. `parent_id` carries the owning resource's
  id when that endpoint needs it (a textbook block's document id, a memo
  entry's workspace id). `source` names which leg(s) produced the hit.

## Learner scope

The request carries no learner identity; the server takes the authenticated
user from auth and passes it as `user_id` to both the keyword and semantic
Ports. A backend returns only shared corpus rows (pack content) and rows that
user owns (their memos), filtering before ranking and `limit`, so another
learner's memos never appear or affect scores.

Not yet wired to an HTTP path: this PR defines the shape contract-first, for
the wiring PR (mode selection in `create_app()`/settings, issue #180 S5) to
serve once the backends exist. It mirrors the `kind`/`id` result shape
`GET /notebook/search` (`contracts/openapi/v0.2/paths/notebook.yaml`)
already uses, adding `mode`, `score` and `source`.

## Drill expected answers are never searchable

Only a drill item's question/prompt text is an eligible source for the
`search_documents`/`search_embeddings` tables (migration
`d7b1e3f5a9c2_v040_scale.py`). A drill item's `expected` answer and its
`reference_solution` are withheld from the search corpus entirely: never
embedded, never indexed, never returned as a result. This is enforced by
each adapter's field allowlist when it writes a row, not by convention or by
this schema — no type in `harness/core/ports/search.py` accepts or returns
an expected-answer or reference-solution value; `SearchHit`, the only result
type, carries only `kind`/`id`/`parent_id`/`score`/`source`.

## Ports

`harness/core/ports/search.py` declares `KeywordSearchBackend`,
`SemanticSearchBackend`, `Reranker` and `EmbeddingProvider` (ADR-0009): core
holds no implementation, only the Protocols an adapter under
`harness/adapters/search/` (a later PR) implements.

## Tests

`tests/contracts/test_search_contracts.py` checks the schemas with
`check_schema`, the `$id` convention, and validates example request/response
instances, including that `SearchHit.to_dict()` conforms to
`response.json`'s result item shape.
