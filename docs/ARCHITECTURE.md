# System Architecture

## Architectural objective
Keep the adaptive learning engine generic while making domain-specific environments, evaluators and content swappable.

This repository is the Harness (SDK): it provides the engine, the UI components and the contracts (ADR-0001).

- Harness code and content data are separate. Subject packs and their evaluation assets are pure data under top-level `contents/<pack-id>/`.
- The harness never imports `contents/`. It receives a pack path and reads it, so `contents/` can later move to another repository.
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
│ Lab Orchestrator | Tool Adapters                                    │
│ Evaluation/Tuning commands: simulate | replay | report              │
└───────────────┬─────────────────────┬───────────────┬───────────────┘
                │                     │               │
           PostgreSQL          Docker sandbox     contents/<pack-id>/
      state + append events    disposable labs    pack + eval assets
                                                  (read by path)
```

The Web Client is part of the SDK: it ships the UI components and renders the pack's layout spec (ADR-0001, ADR-0003).

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
- A new algorithm is added to the harness, never to a pack.
- Registered implementations must not depend on a specific subject (same boundary as AC-G1).
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

### `ai/`
Provider-neutral LLM interface and role-specific prompts.
Do not let provider SDK types leak into domain logic.

### `labs/`
Environment lifecycle and tool adapters.

### `apps/web`
Learner UI, shipped as part of the SDK (ADR-0001).
- The SDK provides the UI components; a pack declares placement, modes, transitions and copy in a declarative YAML/JSON layout spec, and a layout spec renderer places the components accordingly (ADR-0003).
- Packs cannot add components. A new component is added to the SDK.
- The three-pane layout in `docs/UX.md` is the Software Engineering pack's declaration example, not a harness constant.
- The layout spec schema is not yet decided.

### Evaluation commands
Three command families support pack tuning (ADR-0005). Names are provisional and the exact CLI shape is not yet decided.
- `simulate` — measure the learning efficiency of an algorithm configuration with synthetic learners.
- `replay` — recompute recorded sessions under a different pack version; a by-product of the append-only event store.
- `report` — output the primary and secondary metrics per pack.

They read evaluation assets from the pack's `eval/` directory: `golden/` (labelled action logs and expected evidence), `learners/` (synthetic learner definitions), `sessions/` (exported recorded sessions).
The tuning loop is run by a human: the harness reports and compares, a person edits the pack. Synthetic learners cannot measure UX; UX is evaluated only with metrics from real sessions.

## Harness fixed vs pack declared

| Harness fixed | Pack declared |
|---|---|
| Learning loop skeleton (ADR-0004) | UX layout: placement, modes, transitions, copy (ADR-0003) |
| Event schema; every UI operation becomes an event (ADR-0003, ADR-0004) | Algorithm selection by name plus all parameters (ADR-0002, ADR-0004) |
| Every mastery update references evidence IDs (ADR-0004) | Skills, activities, evaluators, visualizations |
| "Correct final state is not mastery" (ADR-0004) | Evaluation assets under `eval/` (ADR-0005) |
| Schema validation of LLM outputs (ADR-0004) | |
| Sandbox and security boundaries | |
| Persistence, including highlight, chat and pane state (ADR-0003) | |
| Abstraction-to-reality linkage mechanism and accessibility requirements (ADR-0003) | |

The harness side carries no tunable defaults (ADR-0002). Anything a pack needs that is not data (a new algorithm, a new UI component) is added to the harness.

## Persistence

### PostgreSQL is both:
1. transactional application database,
2. MVP event store.

Use an append-only `learning_events` table.
Derived current state lives in normalized tables for fast reads.

Do not require a separate event-stream system for MVP.

### Tuning and pack versions
Tuning means raising the pack version; packs are pure data and there is no separate config versioning (ADR-0002).
Every event records `pack_version`, so comparing pack versions is how tuning is evaluated, via `replay` and `report` (ADR-0005).

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

Exact routes may evolve; preserve the domain boundaries.

## Deployment
MVP local-first:
- `docker compose up`
- web
- api
- postgres

Hosted deployment comes later.
