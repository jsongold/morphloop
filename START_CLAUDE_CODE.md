# Initial Claude Code Prompt

Read `CLAUDE.md`, the ADRs under `docs/decisions/` and every file under `docs/` before making architectural decisions.

Your goal is to implement the MVP of **Adaptive Interactive Learning OS / Harness** described in this repository.

Work autonomously toward the acceptance criteria.

## Required workflow

1. Inspect the repository.
2. Compare the existing code, if any, against the design docs.
3. Produce a concise implementation plan mapped to `docs/ACCEPTANCE_CRITERIA.md`.
4. Implement in end-to-end vertical slices.
5. Run tests, type checks and lint after meaningful changes.
6. Keep a short progress file at `docs/IMPLEMENTATION_STATUS.md`:
   - completed acceptance criteria,
   - current work,
   - blockers,
   - important architecture decisions.
7. Do not expand product scope unless required by an acceptance criterion.
8. If a design detail is underspecified, choose the simplest architecture that preserves:
   - domain-independent core,
   - append-only learning events,
   - practical sandbox activities,
   - evidence-based mastery updates,
   - persistent highlight/chat context,
   - abstraction → reality navigation.
9. When you find a conflict between docs, prioritize in this order:
   - `CLAUDE.md`
   - ADRs in `docs/decisions/`
   - `docs/ACCEPTANCE_CRITERIA.md`
   - `docs/MVP.md`
   - `docs/PRODUCT.md`
   - architecture/detail docs
10. Update documentation whenever implementation materially changes an interface.

## First implementation target

Implement the smallest complete vertical slice:

**DNS diagnosis**

This slice is the v0.1 gate (ADR-0012); it is Slice 1 in `docs/IMPLEMENTATION_PLAN.md`.
Keep these contracts from this first slice, because they are expensive to retrofit: definition vs instance (ADR-0007), the event contract (ADR-0008), the core / domain adapter / pack boundary (ADR-0009) and provenance recording (ADR-0010).

A learner should be able to:
1. start/resume a session,
2. receive a DNS practical activity,
3. start an isolated broken-DNS lab,
4. use an in-browser terminal,
5. execute diagnostic commands,
6. have command/output events persisted,
7. recover the service,
8. submit the activity,
9. receive structured evidence,
10. update DNS mastery,
11. view the timeline,
12. use the DNS visualization and its reality mapping: select a step, see the concrete mechanism and run the suggested observation command inside the activity (AC-C1 to AC-C3),
13. highlight a DNS concept and ask AI using the quoted selection,
14. reload the app and recover session/chat/highlight state.

After that slice works, continue in slice order (ADR-0012):
- Slice 2: adaptive loop (diagnostic, policy and a second lab),
- Slice 3: the third lab, the evaluation commands (`simulate` / `replay` / `report`) and holdout tasks (ADR-0006, ADR-0011),
- Slice 4: layout spec generalization and the dummy second pack.

The order of Slice 2 and later may be revised based on the result of Slice 1.

Do not build billing, teams, Kubernetes, a plugin marketplace, or a full authoring CMS.
