# System Architecture

## Architectural objective
Keep the adaptive learning engine generic while making domain-specific environments, evaluators and content swappable.

This repository is the Harness (SDK): it provides the engine, the UI components and the contracts (ADR-0001).

Scope note: this file describes the full target design. The v0.1 acceptance gate is the DNS vertical slice only; the contracts of ADR-0007, ADR-0008, ADR-0009 and ADR-0010 are kept from that first slice, and the rest is gated at v0.2 or later (ADR-0012).

The extension boundary has three layers (ADR-0009):

- **core** — domain independent: the loop, events, the registry mechanism, the pack loader.
- **domain adapter** — domain-specific code. A separate, versioned package in this repository.
- **pack** — data. Packs carry no code (ADR-0004).

Dependencies point one way: a domain adapter depends only on core interfaces. Core imports neither domain adapters nor packs; AC-G1 checks core for this (ADR-0009).

"Packs are pure data" means: a pack contains no code that runs inside the harness process. A pack may contain definitions that are executed inside the learner sandbox (image definitions, command arrays, seed data, fixture settings) (ADR-0009).

- Harness code and content data are separate. Subject packs and their evaluation assets are pure data, in the sense above, under top-level `contents/<pack-id>/`.
- The harness never imports `contents/`. It receives a pack path and reads it, so `contents/` can later move to another repository.
- Core treats pack definitions as read-only data obtained through the pack loader. The database holds no authoritative copy of a definition; a reference cache is allowed (ADR-0007).
- The Web UI and its components (terminal, editor, visualization renderer, chat, side pane, etc.) are part of the SDK.
- The dummy non-software pack used for core-independence tests is a harness test fixture, not `contents/` (ADR-0006).

Decisions are recorded in `docs/decisions/`. If this file and an ADR diverge, the ADR wins.

## Logical components

```text
┌────────────────── Web Client (SDK UI components) ───────────────────┐
│ Layout Spec Renderer: places components per the pack layout spec    │
│ Terminal | Editor | Browser | Visualization | Observability         │
│ AI Chat | Side Context Pane | Highlight/Annotation                  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                          API / WebSocket
                                 │
┌────────────────────────────── Backend ──────────────────────────────┐
│ Session Service                                                     │
│ Adaptive Learning Core                                              │
│  ├─ Skill Graph                                                     │
│  ├─ Assessment Engine                                               │
│  ├─ Learner Model                                                   │
│  ├─ Policy Engine                                                   │
│  ├─ Activity Service                                                │
│  └─ Evidence/Evaluation                                             │
│                                                                     │
│ Algorithm Registry                                                  │
│  └─ learner model | policy | assessment strategy implementations    │
│                                                                     │
│ AI Orchestration                                                    │
│  ├─ Tutor/Coach                                                     │
│  ├─ Content/Activity Generator                                      │
│  └─ Evaluator                                                       │
│                                                                     │
│ Event Service | Highlight/Annotation Service | Pack Loader          │
│ Lab Orchestrator                                                    │
│ Evaluation/Tuning commands: simulate | replay | report              │
│                                                                     │
│ Domain Adapters (separate package; depend on core interfaces only)  │
│  └─ deterministic checks | fixture providers | tool adapters        │
└───────────────┬─────────────────────┬───────────────┬───────────────┘
                │                     │               │
           PostgreSQL          Docker sandbox     contents/<pack-id>/
      state + append events    disposable labs    pack + eval assets
                                                  (read by path)
```

The Web Client is part of the SDK: it ships the UI components and renders the pack's layout spec (ADR-0001, ADR-0003).
Domain Adapters are drawn apart from the core: they hold the domain-specific code and run host-side as reviewed harness code, not as pack content. Core never imports them (ADR-0009).

## Recommended MVP stack

### Frontend
- Next.js
- React
- TypeScript
- xterm.js for terminal
- Monaco editor or CodeMirror for code/editor surfaces
- WebSocket for terminal streaming and live events
- declarative visualization renderer

### Backend
- Python 3.12+
- FastAPI
- Pydantic schemas
- SQLAlchemy + Alembic
- PostgreSQL
- Docker SDK or a narrow lab-runner abstraction

### Why this split
- TypeScript is natural for the browser workspace.
- Python is strong for adaptive logic, evaluation, experimentation and LLM integrations.
- Keep the backend modular but deploy as one service in MVP.

## Modules

### `core/skill_graph`
Generic prerequisite graph and target competency handling.

### `core/learner`
Learner-state interfaces. Mastery model implementations are registered in `core/registry`; the BKT-inspired model is one registered implementation (ADR-0004).

### `core/policy`
Next-best-activity selection. Policy implementations are registered in `core/registry`; the priority heuristic in `docs/LEARNING_LOOP.md` is one registered implementation (ADR-0004).

### `core/registry`
Registry of learner model, policy and assessment strategy implementations (ADR-0004).
- A pack selects each implementation by name and declares all of its parameters. Packs contain no code.
- The harness holds no default values; every tuning value comes from the pack (ADR-0002).
- A new algorithm is added to the harness, never to a pack. Domain-independent implementations (the generic learner model / policy / assessment implementations) live in this core registry; a domain-specific algorithm, if one is ever needed, lives in a domain adapter (ADR-0009).
- Implementations registered in core must not depend on a specific subject (same boundary as AC-G1).
- The registry interface and the pack-side declaration schema are not yet decided. The module name is provisional.

### `core/activity`
Lifecycle of assessment/practice activities.

### `core/evidence`
Evidence schema, aggregation and learner-model update contracts.

### `core/events`
Append-only learning event schema and persistence.

### `core/packs`
Subject-pack loading, validation and versioning.
Receives a `contents/<pack-id>/` directory as a path and validates it against schemas; it never imports pack content (ADR-0001).
A pack must declare every tuning value. How missing values are handled in validation is not yet decided (ADR-0002).
At load time it verifies that every check, fixture and tool the pack references is registered by a domain adapter within the declared version range; if not, the pack is rejected (ADR-0009).
### `ai/`
Provider-neutral LLM interface and role-specific prompts.
Do not let provider SDK types leak into domain logic.

### `labs/`
Environment lifecycle and tool adapters.
Tool adapters and the domain-specific part of the lab lifecycle belong to the domain adapter layer below; how `labs/` relates to that layer is not yet decided (ADR-0009).

### Domain adapter layer
Domain-specific code, packaged separately from core and versioned (ADR-0009). A domain adapter holds:
- deterministic check implementations (e.g. `service_recovered`, `correct_resolver_config`),
- environment fixture providers (the domain-specific part of lab start, reset and destroy),
- tool adapters (terminal, database, etc.; see `docs/SUBJECT_PACK.md`).

Rules:
- A pack refers to adapter capabilities by name, and its manifest declares the adapters it needs with a version range.
- If a referenced check, fixture or tool is not registered, pack loading is refused (see `core/packs`).
- Registration follows the same idea as the algorithm registry: register by name + version, select by name from the pack. Whether there is one registry or one per kind is decided at implementation time.
- Adapter code runs host-side as part of the harness and is reviewed code; it is not pack content.
- Not yet decided: the directory name for domain adapters and its relation to the existing `labs/`.

### `apps/web`
Learner UI, shipped as part of the SDK (ADR-0001).
- The SDK provides the UI components; a pack declares placement, modes, transitions and copy in a declarative YAML/JSON layout spec, and a layout spec renderer places the components accordingly (ADR-0003).
- Packs cannot add components. A new component is added to the SDK.
- The three-pane layout in `docs/UX.md` is the Software Engineering pack's declaration example, not a harness constant.
- The layout spec schema is not yet decided.

### Evaluation commands
Three command families support pack tuning (ADR-0005). Names are provisional and the exact CLI shape is not yet decided.
- `simulate` — measure the learning efficiency of an algorithm configuration with synthetic learners.
- `replay` — recompute from stored data; a by-product of the append-only event store. Scope (ADR-0010):
  - Level 1 (provided first): recompute the learner model from stored evidence, to compare learner-state trajectories under different algorithm implementations or parameters. Deterministic.
  - Level 2 (later): re-run deterministic checks against stored events and rebuild evidence.
  - LLM outputs are never re-executed; stored outputs are used. Re-evaluating with a different LLM evaluator is recorded as a new evaluation, not as replay.
  - Out of scope: policy counterfactuals (what another policy would have selected) and UX differences. Those can only be evaluated with `simulate` or real sessions.
  - Across versions whose `skill_id` set or activity definitions changed, Level 1 covers only the shared `skill_id`s; unmatched evidence is kept and ignored.
- `report` — output the primary and secondary metrics per pack, always stating which means each number came from (ADR-0011).

Primary metric: learner time and number of activities until the learner passes held-out tasks (holdout). Reaching target mastery is no longer the primary metric; it is internal learner-model state (ADR-0011). Secondary metrics: prediction accuracy and calibration of the learner model, hint dependence, time to first useful action (ADR-0005, ADR-0011).

They read evaluation assets from the pack's `eval/` directory:
- `golden/` — labelled action logs and expected evidence,
- `learners/` — synthetic learner definitions,
- `sessions/` — exported recorded sessions,
- `holdout/` — held-out transfer tasks, never served in practice or diagnosis, using fixtures different from the practice tasks, scored by deterministic checks only, and frozen during a tuning comparison (ADR-0011). The number of holdout tasks per skill and how used ones are recorded are not yet decided.

What each means can and cannot measure (ADR-0011):

| Means | Can measure | Cannot measure |
|---|---|---|
| `simulate` (synthetic learners) | Whether an algorithm configuration works, parameter sensitivity, policy comparison within the simulator's assumptions | UX, real learning effect |
| `replay` | Estimate trajectories and prediction accuracy under a changed model configuration | Policy effect, UX |
| Real sessions | Primary, UX and secondary metrics | Version comparison on the same skill (for now) |
| Golden set (`eval/golden/`) | Evaluator regression: signal direction and relative ordering | Exact numeric strength |

The synthetic-learner generative model must use assumptions different from the learner model, to avoid overfitting to the simulator (ADR-0011).
The tuning loop is run by a human: the harness reports and compares, a person edits the pack. Synthetic learners cannot measure UX; UX is evaluated only with metrics from real sessions.
Constraint: there is currently one real learner (the owner), who cannot learn the same skill twice. Until that changes, UX tuning is a subjective judgment informed by metrics, and no statistical claims are made about UX (ADR-0011).

## Harness fixed vs pack declared

| Harness fixed | Pack declared |
|---|---|
| Learning loop skeleton (ADR-0004) | UX layout: placement, modes, transitions, copy (ADR-0003) |
| Event schema; every UI operation becomes an event (ADR-0003, ADR-0004) | Algorithm selection by name plus all parameters (ADR-0002, ADR-0004) |
| Every mastery update references evidence IDs (ADR-0004) | Skills, activities, evaluators, visualizations |
| "Correct final state is not mastery" (ADR-0004) | Evaluation assets under `eval/`, including `holdout/` (ADR-0005, ADR-0011) |
| Schema validation of LLM outputs (ADR-0004) | Required domain adapters and their version ranges, in the manifest (ADR-0009) |
| Sandbox and security boundaries; pack-derived executables run only in the sandbox (ADR-0009) | |
| Persistence, including highlight, chat and pane state (ADR-0003) | |
| Abstraction-to-reality linkage mechanism and accessibility requirements (ADR-0003) | |
| Event contract: ordering, atomicity, idempotency, causation, rebuild (ADR-0008) | |
| Separation of pack definitions and runtime instances, with ID rules (ADR-0007) | |
| Provenance recording on events (ADR-0010) | |

The harness side carries no tunable defaults (ADR-0002). Anything a pack needs that is not data is added to the harness: domain-independent pieces (a new generic algorithm, a new UI component) to core or the SDK, domain-specific code (a check, a fixture provider, a tool adapter) to a domain adapter (ADR-0009).

## Persistence

### PostgreSQL is both:
1. transactional application database,
2. MVP event store.

Use an append-only `learning_events` table.
Derived current state lives in normalized tables for fast reads.

Do not require a separate event-stream system for MVP.

### Event contract
Positioning: a middle ground — neither a full event sourcing framework (asynchronous projections, CQRS) nor an ordinary database with an audit log. Learning state (`LearnerSkillState`, session resume state, highlights, chat) is a projection that can be rebuilt from events. Single PostgreSQL, synchronous projections (ADR-0008).

- Ordering: `learning_events` has a DB-assigned, monotonically increasing `position`. Timeline and rebuild order use `position`. `occurred_at` is the source time and is not used for ordering; `recorded_at` is the DB write time.
- Atomicity: appending an event and updating its projections happen in the same DB transaction.
- Idempotency: events sent by the client and the terminal bridge carry an `idempotency_key`; resending the same key returns the existing event instead of creating a new one.
- Concurrency: learner-skill updates for the same learner are serialized. The mechanism is decided at implementation time.
- Causation: the envelope carries `causation_id` (the event that directly caused this one) and `correlation_id` (a bundle such as one attempt or one chat round trip).
- Append-only is enforced at the DB level: UPDATE and DELETE on `learning_events` are forbidden, not only by application convention. The mechanism is decided at implementation time.
- Rebuild: the harness provides a way to drop projections and rebuild them from events; a test checks that the rebuilt state equals the live state.
- Schema evolution: stored events are never rewritten; `event_version` is used and events are upcast at read time.
- Known caveat: under concurrent transactions, `position` assignment order can differ from commit order. This is harmless while projections are updated synchronously in the same transaction and no asynchronous consumer follows `position`; revisit when one is introduced.

Details of the envelope are in `docs/EVENT_SCHEMA.md`.

### Tuning and pack versions
Tuning means editing the pack and raising its version; there is no separate versioning for tuning versus content (ADR-0002, ADR-0010).

- `pack_version` is a human-facing label. The pack content hash, computed from the pack directory contents, is the source of truth for pack identity; an edit that forgot to bump the version is still detected by the hash. Whether `eval/` is included in the hash is not yet decided (ADR-0010).
- Provenance is recorded on events: harness version; `pack_id`, `pack_version` and pack content hash; `name@version` of the registry implementations used; domain adapter versions; lab image digest and fixture id at lab start; evaluator rubric id and version; and, for events that used an LLM, provider, model, prompt version and generation parameters. Principle: record once at session start, and record anything that can change on the event where it occurs. The exact assignment of items to events is not yet decided (ADR-0010).
- The comparison report shows the diff between two pack contents per top-level directory (`algorithm/`, `ux/`, `activities/`, `evaluators/`, etc.), so that a result can be read as "what was changed" (ADR-0010).
- When a pack is updated, learner state is recomputed from stored evidence under the new configuration, through the same path as `replay` Level 1 (ADR-0010).

## LLM boundaries
Use separate logical roles even if the same model/provider is used:
- **Tutor** — helps but should not leak full solutions prematurely.
- **Generator** — creates activity/remediation candidates.
- **Evaluator** — converts observed actions into structured evidence.
- **Summarizer** — optionally compresses long session context.

All state-changing outputs must be schema validated.

## Context assembly for AI
The chat assistant should receive a bounded context:
- active mission,
- target skills,
- learner-selected highlight(s),
- currently visible concept,
- current environment metadata,
- recent relevant actions,
- learner mastery summary,
- retrieved subject-pack references.

Do not dump the full event log into every prompt.

## Sandbox model
Each practical activity gets an isolated disposable environment.

MVP:
- Docker container or Docker Compose project,
- CPU/memory/time limits,
- no privileged mode,
- no host filesystem mount except explicit read-only fixtures,
- no host Docker socket,
- controlled network,
- destroy/reset supported.

## Security boundaries
The learner terminal is untrusted input.
Never interpolate terminal input into host shell commands.
All environment operations must use container APIs or explicit command arrays.

Pack content is also untrusted for execution purposes (ADR-0009):
- Anything executed that originates from a pack (container image definitions, `command_exit` command arrays, seed data, fixture settings) runs only inside the learner sandbox.
- Never pass a pack-derived string to a shell on the host.
- Domain adapter code runs host-side as part of the harness; it is reviewed code, not pack content.
- Prompts may live in a pack; LLM output that affects learner state still goes through schema validation.

## API sketch

```text
POST /sessions
GET  /sessions/{id}

POST /assessments/start
POST /activities/next
GET  /activities/{id}

POST /highlights
GET  /sessions/{id}/highlights

POST /chat/messages
GET  /sessions/{id}/chat

WS   /labs/{lab_id}/terminal

POST /activities/{id}/submit
GET  /learners/{id}/skills
GET  /sessions/{id}/timeline
```

In the `/activities/{id}` routes, `{id}` is the id of an `ActivityAttempt` (`att_...`), a runtime instance, not a pack-internal `ActivityDefinition` id. Route names are unchanged here (ADR-0007).

Exact routes may evolve; preserve the domain boundaries.

## Deployment
MVP local-first:
- `docker compose up`
- web
- api
- postgres

Hosted deployment comes later.
