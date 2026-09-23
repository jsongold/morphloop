# contracts/schemas/llm/

JSON Schema for structured LLM outputs. Each schema is the output contract of
one LLM role. The pack names it in its LLM implementation declaration (prompt
id and version, model, output schema; ADR-0013). The harness validates every
raw output against its schema before using it. An output that fails
validation changes no state (AC-E4).

## Layout

`<output_name>/<major>.json`, the same layout as event payloads. The
`$id` follows the repo convention (`https://morphloop.dev/contracts/` + path).

| Schema | Role | Recorded as |
|---|---|---|
| `learner_model.update/1.json` | LLM learner model, one (learner, skill) update | `learner_skill.updated` v1 |
| `evaluator.judgment/1.json` | LLM evaluator, judgment over one Evaluation | `evaluation.completed` v1 (`success`, `rationale`) + one `evidence.created` v1 per evidence item |
| `tutor.reply/1.json` | Tutor, one chat reply | `assistant.message_generated` v1 |
| `memo_summarizer.note/1.json` | Memo summarizer, one learning note per highlight thread | `memo.recorded` v1 (`title`, `body`) |
| `generator.activity_candidate/1.json` | Generator, one candidate activity from a Template (`authoring`, ADR-0014) | not an event; materialized into a pack Definition plus a provenance sidecar (ADR-0016) |

Versioning follows event payloads: a released file is never edited. A change
is a new major file.

## Mapping onto events

- An output holds only what the model decides. The harness adds ids,
  references to the request context and provenance. It copies the output
  fields verbatim into the event payload fields of the same name. Examples of
  harness-added fields: `pack_id`, `skill_id`, `previous`, `evidence_id`,
  `evaluation_id`, `message_id`, `mode`, `provenance`.
- Where an output field is a payload field, the output schema `$ref`s that
  payload property (`.../payloads/<type>/1.json#/properties/<field>`), so the
  two cannot drift. Output v1 maps onto payload v1. A new payload major needs a
  new output major.
- The full prompt is a system log and never appears in an output or an event
  (ADR-0016).
- `tutor.reply` has no `mode`. The harness decides the mode (e.g. `hint`,
  `explain`) from the request context before the call and records it, so the
  model cannot leave hint mode (AC-E3). A schema cannot keep the reference
  solution out of the reply text. That guarantee comes from keeping the
  reference solution out of the tutor context (AC-J6, ADR-0014).
- "Correct final state is not mastery" is expressed through evidence `signal`,
  `dimension` (pack vocabulary) and `rationale`, and through the skill state's
  `hint_dependency`, `transfer_score` and `misconceptions`. These are all
  existing payload fields.

## Generator candidate

The candidate is self-contained. It does not `$ref` the pack activity
Definition schema, because it is an input to materialization, not a
Definition. It carries only model decisions:

- title and mission;
- registered fixture and check ids from a domain adapter (ADR-0009), each with
  `params` as a name/value list;
- hints;
- the secret reference solution (an explanation plus argv steps run only
  inside the learner sandbox).

The harness adds the fields the Template declares: target skills, difficulty,
evaluator and timing. It also adds the fields it assigns: the Definition id,
the content hash and provenance. It merges these with the candidate and
validates the result against the pack Definition schema. Only then is the
Definition stored.

## Structured-output compatibility

These schemas are meant to be bundled (external `$ref`s inlined) and passed to
a provider as the structured-output schema. `tests/contracts/test_llm_contracts.py`
enforces these rules on the files in this directory:

- The root is an object. Every object is closed (`additionalProperties: false`),
  declares `properties`, and lists every property in `required`. An optional
  value is expressed as nullable, not as an absent key.
- Unions use `anyOf` only. There is no `oneOf`, `allOf`, `not`, `if`/`then`/`else`,
  `patternProperties`, `propertyNames`, `prefixItems`, `unevaluated*` or `dependent*`.
- Open maps are avoided. Adapter parameters are a name/value list, not an
  object.

Known exception: `common/skill-state.json` (referenced for the new state) has
optional properties. A provider that requires every property to be listed in
`required` needs the adapter to transform it when bundling.

Providers may ignore some constraints: `pattern`, `format`,
`minimum`/`maximum`, `minLength`, `minItems`, `uniqueItems`, `const`. Only the
harness-side validator is authoritative for them. The harness also enforces
these semantic rules that no schema can express:

- `learner_model.update.evidence_ids` must be a subset of the evidence given in
  the call.
- `evaluator.judgment` evidence `skill_id`s must be skills under test in the
  Definition. `supporting_event_ids` must be events given in the call.
  `dimension` must be in the pack vocabulary.
- `tutor.reply.references` may only point at highlights and events given in
  the call.
- `generator.activity_candidate` fixture and check ids must be registered in a
  domain adapter, and their params must satisfy the item's parameter spec.

## Tests

Examples live in `tests/contracts/fixtures/llm/{valid,invalid}/` as
`{description, schema, instance}` records, plus `expected_error_paths` for the
invalid ones. `tests/contracts/test_llm_contracts.py` checks the schemas,
the conventions and the examples. It also checks that each learner-model,
evaluator and tutor example embeds into a valid event payload.
