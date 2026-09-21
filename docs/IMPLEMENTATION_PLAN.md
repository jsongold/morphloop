# Implementation Plan

## Phase 0 — Repository skeleton
Deliver:
- monorepo/repo structure,
- local Docker Compose,
- frontend,
- API,
- PostgreSQL,
- migrations,
- health checks,
- core schema packages,
- test harness.

Exit:
- one command starts the stack,
- CI runs tests/type checks.

## Phase 1 — Event and domain foundation
Deliver:
- core entities,
- append-only event store,
- session lifecycle,
- highlight persistence,
- chat persistence,
- subject-pack loader and schema validation; the loader receives a `contents/<pack-id>/` path and the harness never imports `contents/` (ADR-0001),
- `pack_version` recorded on every event (ADR-0002).

Exit:
- events can reconstruct a simple session,
- dummy pack loads from the harness test fixtures, not from `contents/` (ADR-0006).

## Phase 2 — Adaptive loop
Deliver:
- skill graph,
- learner skill state,
- diagnostic activity lifecycle,
- evidence objects,
- algorithm registry for learner model / policy / assessment strategy (ADR-0004),
- initial learner model and simple policy engine as registry implementations,
- configuration of implementations and all parameters from the pack declaration, with no harness defaults (ADR-0002, ADR-0004),
- `simulate`: synthetic learners measuring learning efficiency of a pack's algorithm configuration (ADR-0005; command name provisional).

Exit:
- simulated evidence changes next activity selection,
- changing a pack parameter changes the simulation result.

## Phase 3 — Practice environment
Deliver:
- Docker lab runner,
- xterm.js terminal,
- PTY/WebSocket bridge,
- command/output events,
- reset/destroy,
- security constraints.

Exit:
- learner completes deterministic terminal task in browser.

## Phase 4 — Software Engineering pack
Deliver:
- DNS lab,
- HTTP lab (concrete fixture not yet decided, ADR-0006),
- DB indexing lab,
- skill definitions,
- evaluators,
- references,
- diagrams/animation specs,
- algorithm and parameter declarations for the three skills (ADR-0004),
- `replay`: recompute a recorded session under a different pack version (ADR-0005),
- `report`: per-pack primary and secondary metrics (ADR-0005),
- pack `eval/` assets (`golden/`, `learners/`, `sessions/`) (ADR-0005).

TCP is deferred (ADR-0006).
`replay` and `report` are placed here because they need the event store (Phase 1), the registry (Phase 2) and real recorded sessions from real activities. Command names are provisional.

Exit:
- adaptive loop works with real activities,
- a recorded session replays under a second pack version and the report compares them.

## Phase 5 — Contextual AI
Deliver:
- provider interface,
- tutor role,
- evaluator role,
- structured outputs,
- highlight quoting,
- context builder.

Exit:
- AI references current mission/selection and evaluator emits evidence.

## Phase 6 — UX integration
Deliver:
- layout spec renderer: SDK UI components arranged by the pack's declarative layout spec (ADR-0001, ADR-0003; spec schema not yet decided),
- Software Engineering pack layout declaration covering the items below,
- main practice pane,
- side context pane,
- persistent chat,
- abstraction→reality links,
- session timeline.

Exit:
- all acceptance criteria pass.

## Development order rule
Prioritize end-to-end vertical slices over broad incomplete infrastructure.

Preferred slice:
`DNS diagnostic → terminal → events → evaluator → mastery update → next mission`

before implementing every domain model feature.

## Avoid
- premature multi-agent architecture,
- distributed queues,
- generalized plugin marketplaces,
- elaborate design system,
- full curriculum authoring UI,
- sophisticated ML recommender,
- production cloud orchestration.

Prove the adaptive learning loop first.
