# contracts/schemas/pack/

JSON Schemas (draft 2020-12) for the files of a subject pack under
`contents/<pack-id>/`. Scope is what the v0.1 DNS slice needs (ADR-0012). A pack
file may be YAML or JSON. The schema applies to the parsed document.

## Files

| Schema | Pack file kind | Purpose |
|---|---|---|
| `manifest.json` | `manifest.yaml`/`.json` at the pack root | identity, domain adapters and version ranges, registry selections with every parameter, generation defaults, file index |
| `skill.json` | `skill` | SkillDefinition |
| `activity-definition.json` | `activity` | finalized ActivityDefinition (authored or generated, same shape) |
| `reference-solution.json` | `reference_solution` | secret fix for one activity, in its own file |
| `environment.json` | `environment` | EnvironmentDefinition: adapter fixture + image digest + params |
| `activity-template.json` | `activity_template` | Generator input, valid for all three timings |
| `generation-record.json` | `generation_record` | `authoring` generation record next to the generated files |
| `evaluator.json` | `evaluator` | rubric for the LLM evaluator (semantic dimensions, misconceptions) |
| `visualization.json` | `visualization` | `sequence` diagram with reality mapping (AC-C1 to AC-C3) |
| `reference.json` | `reference` | short concept content for the side pane, media by URI + hash |
| `layout.json` | `layout` | provisional, permissive layout spec (see below) |
| `defs.json` | none | shared pack-only defs: timing, relative path, version range, argv, image, media, document hash |
| none | `prompt` | plain text. The index entry carries `prompt_id` + `prompt_version` |

Harness-wide shapes are `$ref`'d from `schemas/common/` (ids, content hash,
versions, provenance). They are not redefined here.

## Conventions

- **File index.** `manifest.files` maps each pack-relative path to its `kind`.
  The kind selects the schema. The Importer refuses a pack when a file is not
  indexed or an indexed file is missing. The manifest itself is not indexed.
  Paths are POSIX, relative and contain no `..`.
- **Pack format.** `pack_format: 1` means this schema set. A breaking change
  adds a new value and new schema files. A released file is never changed in
  a breaking way.
- **Document hash.** A Definition's identity is its id plus
  `sha256:` + hex SHA-256 of the RFC 8785 (JCS) canonical JSON of the parsed
  document. The hash does not depend on YAML or JSON formatting. A document
  never contains its own hash. The hash is computed by the Importer or by the
  generator CLI. `activity.started.activity_definition_hash`,
  `lab.started.environment` and `evaluation.completed.evaluator` carry it.
  Cross-document bindings use `{path, content_hash}`.
- **Pack content hash (ADR-0010).** The pack's identity covers every file in
  the pack, including `eval/` and the manifest: `sha256:` + hex SHA-256 of
  the RFC 8785 canonical JSON of the sorted list of
  `{path, content_hash}` for all files, where each file's `content_hash` is
  the SHA-256 of its raw bytes. Any change to any file, holdout assets
  included, yields a new pack identity. Computed by the Importer.
- **No harness defaults (ADR-0002).** Every tunable value is required where it
  is used. Examples: `mastery_threshold`, `max_regenerations`, every LLM
  parameter, reference-solution step timeouts.
- **Registry (ADR-0004, ADR-0013).** `registry.<role>` is
  `{implementation: name@version, llm, output_schema, context_budget_tokens, options}`.
  `llm` is exactly `common/provenance.json#/$defs/llm`, so events copy it
  verbatim. Roles are a closed set: `learner_model` is required.
  `evaluator`/`generator`/`policy`/`assessment` must declare an `output_schema`
  (an `$id` under `schemas/llm/`). `tutor` may omit it. Every v0.1
  implementation is an LLM implementation. A deterministic implementation
  added later gets its own selection shape as an alternative. `options` holds
  implementation-specific parameters, which the implementation validates.
- **Domain adapters (ADR-0009).** The pack refers to fixtures, checks and
  tools only by adapter item id (`<adapter_id>.<name>`). Their params are
  opaque objects. The adapter validates them at import. A param named `argv`
  is always a sandbox argv. DNS specifics appear only as adapter ids and
  params.
- **Sandbox commands.** Every command is an argv array (`defs.json#/$defs/sandbox_argv`),
  run only inside the learner sandbox and never through a host shell.
  `argv[0]` may not contain whitespace, so a whole command line is rejected.
- **Large assets (ADR-0015).** Lab images are referenced by `repository` +
  `digest` (no tags). Media are referenced by `uri` + `sha256` + `media_type` +
  `alt`.

## Decisions

**Reference solution handling (AC-J6, ADR-0014).** The solution is its own
document of kind `reference_solution`. The activity binds to it by path and
document hash, so the activity's hash covers the solution, but the solution is
never serialized with the activity. Withholding is decided by whitelist, not
blacklist:

- Only these activity fields may reach the learner, or the tutor during an
  unfinished attempt: `title`, `activity_type`, `skills`, `difficulty`,
  `instructions`, `hints`, `remediation`.
- These kinds are withheld from both: `reference_solution`, `environment`
  (its params may describe the injected fault), `activity_template` (its brief
  may describe the fault), `generation_record` and `evaluator` (its guidance
  may name the root cause).
- `skill`, `visualization`, `reference` and `layout` are learner-facing.

A lab-backed activity (one with `environment`) must have at least one check
and a reference solution. This is enforced in the schema.

**Template timing (ADR-0014).** The manifest declares
`content_generation.default_timing` and `max_regenerations`. It also needs a
`generator` selection when any template is indexed; the schema enforces this.
A Template may override the timing with `timing`. All three values are valid
in the schemas. The v0.1 Importer refuses `pooled` and `just_in_time` because
they are not implemented yet; the pool size and refill settings will be added
when they are designed. A holdout Template (`holdout: true`) with an explicit
non-`authoring` timing is rejected by the schema. A holdout Template that
inherits a non-`authoring` default is rejected by the Importer, which can see
both files. The Template declares every non-model field: skills, difficulty,
evaluator, tools, image, remediation and the solution step timeout. It also
bounds the Generator to `allowed_fixtures` and `allowed_checks`. The Generator
output is `schemas/llm/generator.activity_candidate/1.json`. Materialization
merges the Template with the candidate. It turns the candidate's name/value
params into objects and writes an activity, an environment and a reference
solution.

**Generation record (ADR-0016 section 6).** The minimal in-house file sits next
to the generated files. It holds the Template id and hash, the generator
implementation plus LLM provenance, `generated_at`, the outputs with their
hashes (exactly one activity), and the validation steps (all `passed`). A
lab-backed output requires `checks_fail_in_broken_state` and
`checks_pass_after_solution`. Rejected candidates are summarized
(attempt, failed step, reason) without their content, which covers "rejection
is recorded" (AC-J7) without storing secret candidate solutions. The record
exists only for `authoring`. Runtime timings use events (v0.2+).

**Layout (ADR-0003, ADR-0012).** `layout.json` accepts any JSON object, and
that is deliberate. ADR-0012 forbids fixing the generic layout schema in v0.1.
A structural schema written now would become a de facto contract for the
standard UI and for custom UIs, before the first implementation exists to
extract it from. Requiring an object still lets the Importer hash and project
the file and lets the API serve it. The standard UI owns its interpretation
and must tolerate unknown keys. A custom UI may ignore it. Slice 4 (AC-H1)
replaces this file with a real schema. This is the only pack schema without
`additionalProperties: false`. The one structured key is the optional
top-level `toc` (a book-like table of contents: `chapters[].title` and
`chapters[].items[]` of `{kind, id}`, kind in activity / reference /
visualization); the Importer rejects an item whose id is not a Definition of
that kind in the pack. It is navigation only and never locks an item.

**Visualization.** Only `diagram.type: "sequence"` is supported. It is not a
generic visualization language. A step's optional `reality` holds `mechanism`,
`observe` (argv plus purpose, run in the attempt's lab terminal) and
`artifacts` (a display-only locator of observable state). At least one step
must carry `reality`. Step ids are what `visualization.step_selected` records.
`version` is its `content_version`. Optional `environment_bindings` names the
environment params whose literal values the visualization shows (host, port,
path); the authoring generator writes a per-activity copy with the generated
environment's values (`visualizations/<activity-id>.<viz-id>.json`, recorded as
a `visualization` output) and the activity names that copy.

**Checks vs rubric.** The deterministic success checks, with their params,
live on the activity. The rubric holds only what the LLM judges on top of the
observed facts. A rubric without `semantic` is deterministic-only, as holdout
will require.

## Enforced by the Importer, not by the schemas

These rules cross files or need a registry:

- indexed files exist, and every file is indexed;
- ids are unique per kind;
- referenced skills, activities, environments, evaluators, visualizations,
  references and prompts exist;
- visualization `from`/`to` name declared actors;
- a visualization an activity names shows that activity's lab: each
  `environment_bindings` entry equals the activity environment's param
  (`binding_mismatch`);
- adapter items are registered by an adapter in `domain_adapters` whose version
  satisfies the range;
- the registry implementation is registered and its `options` are valid;
- document hashes match;
- holdout Templates do not inherit a non-`authoring` default;
- no `pooled`/`just_in_time` in v0.1.

## Tests

`tests/contracts/test_pack_contracts.py` checks every schema with
`check_schema` and checks the `$id` convention. It validates the DNS example
pack in `tests/contracts/fixtures/pack/valid/dns-pack/` through its manifest
index, and it verifies the hash bindings (activity to solution, generation
record to outputs and template) and that prompts and output schemas resolve.
It also checks that the cases in `tests/contracts/fixtures/pack/invalid/`
fail at the expected paths.
