# Implementation Plan

The plan is organized by vertical slices, not horizontal phases (ADR-0012).
The design direction of ADR-0001 to ADR-0011 is unchanged; only the implementation order and the per-version gates changed.
Slice 1 is the v0.1 gate. The order of Slice 2 and later may be revised based on the result of Slice 1.
The gate version of each acceptance criterion is listed in `docs/ACCEPTANCE_CRITERIA.md`.

## Slice 0 — Repository skeleton
Keep this minimal; build only what Slice 1 needs.

Deliver:
- repo structure with the core, domain adapter and pack kept as separate layers (ADR-0009; the domain adapter directory name is not yet decided),
- local Docker Compose,
- API,
- web frontend,
- PostgreSQL,
- migrations,
- health checks,
- test harness,
- CI.

Exit:
- one command starts the stack,
- CI runs tests/type checks.

## Slice 1 — DNS vertical slice (v0.1 gate)
Deliver:
- definition vs runtime instance separation: `ActivityDefinition` in the pack, `ActivityAttempt` / `LabInstance` / `Evaluation` / `Evidence` in the DB, each referencing its definition id and `pack_version`; events carry `attempt_id` and `activity_definition_id` (ADR-0007),
- event store under the event contract: DB-assigned `position`, `recorded_at`, `idempotency_key`, `causation_id`, `correlation_id`, append-only enforced at the DB level, event append and projection update in the same transaction, serialized learner-skill updates per learner, and a rebuild command that recreates projections from events (ADR-0008),
- three-layer boundary: core, a DNS domain adapter (deterministic checks, environment fixture provider, terminal tool adapter) and the pack; the core imports neither the adapter nor the pack; the pack manifest declares the adapters it requires and loading is rejected when a referenced check/fixture/tool is not registered; pack-derived commands run only inside the learner sandbox (ADR-0009),
- provenance recording on events: harness version, `pack_id`, `pack_version`, pack content hash, registry implementation `name@version`, domain adapter version, lab image digest and fixture id, evaluator rubric id/version, LLM provider/model/prompt version/parameters (ADR-0010; which event carries which item is not yet decided),
- pack loader and schema validation; the loader receives a `contents/<pack-id>/` path and the harness never imports `contents/` (ADR-0001),
- algorithm registry mechanism and loading of the pack declaration, with one learner model implementation and no harness default values (ADR-0002, ADR-0004, ADR-0012); policy and assessment strategy are Slice 2,
- Docker lab runner with start/reset/destroy and the security constraints (no host filesystem, no Docker socket),
- xterm.js terminal with PTY/WebSocket bridge, command/output events,
- deterministic checks for the DNS activity,
- LLM provider interface with tutor and evaluator roles; outputs that affect learner state are schema-validated,
- evidence objects and DNS mastery update referencing evidence IDs,
- highlight and chat persistence, highlight quoting and the context builder,
- DNS visualization and reality mapping (AC-C1 to AC-C3),
- session timeline ordered by `position`,
- session resume after reload/restart,
- SE pack content for DNS: skill definition, activity definition, broken-DNS fixture, evaluator, references, visualization spec,
- UI built from SDK UI components, with the SE pack layout held as pack data; the generic layout spec schema is not fixed in this slice (ADR-0003, ADR-0012).

Exit:
- a learner completes the v0.1 chain described in `docs/ACCEPTANCE_CRITERIA.md` end to end,
- every acceptance criterion gated at v0.1 passes,
- a test shows that projections rebuilt from events equal the running state (AC-F6),
- a test shows that resending the same `idempotency_key` creates no new event (AC-F5),
- a check shows that the core imports neither the domain adapter nor the pack (AC-G1).

## Slice 2 — Adaptive loop (v0.2 or later)
Deliver:
- skill graph and target profile,
- diagnostic assessment lifecycle (`AssessmentRun`, ADR-0007),
- policy and assessment strategy as registry implementations, configured entirely from the pack declaration (ADR-0002, ADR-0004),
- a second lab with its domain adapter parts, skill definition, evaluator and algorithm declarations. Which of the HTTP lab and the DB indexing lab comes first is not decided; the HTTP fixture is not yet decided (ADR-0006).

Exit:
- evidence changes the next activity selection (AC-A2, AC-A4) across two lab-backed skills.

## Slice 3 — Third lab, evaluation commands and holdout (v0.2 or later)
Deliver:
- the third lab (the remaining one of HTTP and DB indexing); the DB index visualization with its `EXPLAIN` mapping belongs to the DB indexing lab (AC-C4),
- diagnostic across the three skills (AC-A1, ADR-0006),
- `simulate`: synthetic learners exercising the pack's algorithm configuration; the generator model uses assumptions separate from the learner model (ADR-0005, ADR-0011),
- `replay` Level 1: recompute the learner model from stored evidence and compare configurations (ADR-0010),
- `report`: primary and secondary metrics per pack, showing which means each number came from (ADR-0011),
- pack `eval/` assets: `golden/`, `learners/`, `sessions/`, `holdout/` (ADR-0005, ADR-0011),
- holdout tasks: never served in practice or diagnostics, scored by deterministic checks only (ADR-0011; the number per skill and the record of used holdouts are not yet decided).

TCP is deferred (ADR-0006). Command names are provisional (ADR-0005).

Exit:
- AC-A1, AC-C4 and AC-I1 to AC-I4 pass.

## Slice 4 — Layout spec generalization and dummy pack (v0.2 or later)
Deliver:
- generic layout spec schema extracted from the Slice 1 to 3 implementation, and a renderer that arranges SDK UI components from the pack's declaration (ADR-0003, ADR-0012),
- SE pack layout expressed in that spec, with no hard-coded Software Engineering layout in the harness (AC-H1),
- dummy non-software pack in the harness test fixtures, not under `contents/` (AC-G3, ADR-0006).

Exit:
- all acceptance criteria pass.

## Development order rule
Prioritize end-to-end vertical slices over broad incomplete infrastructure.

Preferred slice:
`DNS diagnostic → terminal → events → evaluator → mastery update → next mission`

before implementing every domain model feature.
Slice 1 covers this chain up to the mastery update; `next mission` is Slice 2 (ADR-0012).

## Avoid
- premature multi-agent architecture,
- distributed queues,
- generalized plugin marketplaces,
- elaborate design system,
- full curriculum authoring UI,
- sophisticated ML recommender,
- production cloud orchestration.

Prove the adaptive learning loop first.
