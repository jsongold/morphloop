# contracts/schemas/pack/v2/

JSON Schemas (draft 2020-12) for pack v2 (`pack_format: 2`). They live beside
the v1 schemas in `../`, which stay unchanged until v1 is removed. Conventions
(`$id`, YAML or JSON files, relative paths, no harness defaults) are those of
`../README.md`. Shared shapes are `$ref`'d from `schemas/common/` and
`../defs.json`; the lab environment is `../environment.json`.

## Files

| Schema | Listed in manifest under | Purpose |
|---|---|---|
| `manifest.json` | none (pack root) | identity, domain adapters, file lists per kind. No layout, registry or templates |
| `labels.json` | `labels` (one file) | label vocabulary the pack declares |
| `topic.json` | `topics` | one topic tree per file (`id`, `title`, `description`, `docs[]`, `topics[]`) |
| `textbook-doc.json` | `textbooks` | teaching text: `blocks[]` of `{id, body, labels}` |
| `drill-item.json` | `drills` | question + expected answer, `answer_mode` text / choice / artifact |
| `artifact-spec.json` | `artifacts` | `{id, type, labels, spec}`; `type: lab` fixes the spec shape |
| `llm-role.json` | `llm_roles` | `{role, model, temperature?, max_tokens?, prompt, output_schema?}`; one LLM call the pack configures |
| `defs.json` | none | label shapes |

## Labels over enums

Classification is by label, never by a domain enum. Enums exist only for values
that change SDK behavior (`answer_mode`, the `sys:` labels).

- A label is `name` or `namespace:name`, lowercase.
- `sys:` is reserved for the SDK. The only one is `sys:holdout` (pack-authored
  items only). Any other `sys:` label is rejected by the schema.
- `topic:<topic id>` attaches an item to a topic. These labels come from the
  topic tree, not the vocabulary.
- Every other label must be in `labels.json`. Neither `sys:` nor `topic:` can be
  declared there.

## Decisions

- **Topics** hold no prerequisites, mastery thresholds or evidence dimensions.
  A topic's optional `docs` is an ordered, unique list of textbook doc ids:
  the pack's declared reading order for that topic.
- **Text blocks** are paragraph-level. A block id is assigned once when the
  document is finalized and never changes; highlights anchor to it. `body` is
  CommonMark. An artifact is embedded as `::artifact{type=<type> ref=<id>}`
  in a body; the schema treats the body as a string.
- **Drill items**: `choices` is required for `choice` and forbidden otherwise;
  `artifact_ref` is required for `artifact` and forbidden otherwise. Pack and
  generated items share the shape and differ by label.
- **Artifact type** names a subclass registered in the domain adapter layer.
  It is not an enum. `spec` is validated by that subclass, except `lab`: its
  spec is `{environment, allowed_fixtures[], allowed_checks[]}`, where the
  generator may compose only the listed adapter items (ADR-0014).
- **LLM roles** configure the LLM calls a pack uses (chat assistant, generation,
  gap judgment, scheduling, ...). `role` is a name the pack chooses; the
  harness holds no role enum (ADR-0002, ADR-0018) and does not interpret it.
  `prompt` names a pack-internal Markdown file; `model`, `temperature` and
  `max_tokens` are tuning values the pack owns in full. `output_schema` is
  optional: a role with nothing consuming its output yet declares no schema.

## Enforced by the Importer, not by the schemas

- listed files exist and every file is listed;
- ids are unique per kind, block ids unique per document, topic ids unique
  across the pack;
- every label is `sys:holdout`, `topic:<existing topic id>` or in the vocabulary;
- a `choice` item's `expected` is one of its `choices`;
- `artifact_ref` and `::artifact{...}` refs name an artifact spec of that type;
- artifact `type` is registered, and a lab's fixtures and checks are registered
  by an adapter in `domain_adapters`; the lab environment's `fixture` is in
  `allowed_fixtures`;
- every id in a topic's `docs` names an existing textbook doc, and every
  textbook doc is listed under exactly one topic's `docs`;
- each llm role's `prompt` file exists; role names are unique across a pack's
  `llm_roles` files.

## Tests

`tests/contracts/test_pack_v2_contracts.py` checks every schema and its `$id`,
validates `tests/contracts/fixtures/pack-v2/valid/dns-pack/` through its
manifest, and checks that `tests/contracts/fixtures/pack-v2/invalid/` cases fail
at the expected paths.
