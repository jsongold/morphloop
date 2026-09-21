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
- subject-pack loader and schema validation.

Exit:
- events can reconstruct a simple session,
- dummy pack loads.

## Phase 2 — Adaptive loop
Deliver:
- skill graph,
- learner skill state,
- diagnostic activity lifecycle,
- evidence objects,
- initial learner model,
- simple policy engine.

Exit:
- simulated evidence changes next activity selection.

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
- DB indexing lab,
- skill definitions,
- evaluators,
- references,
- diagrams/animation specs.

Exit:
- adaptive loop works with real activities.

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
