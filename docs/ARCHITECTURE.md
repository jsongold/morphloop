# System Architecture

## Architectural objective
Keep the adaptive learning engine generic while making domain-specific environments, evaluators and content swappable.

## Logical components

```text
┌──────────────────────────── Web Client ────────────────────────────┐
│ Main Practice Pane | Side Context Pane | Persistent AI Chat       │
│ Terminal | Editor | Browser | Visualization | Observability       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                         API / WebSocket
                                │
┌──────────────────────────── Backend ────────────────────────────────┐
│ Session Service                                                    │
│ Adaptive Learning Core                                             │
│  ├─ Skill Graph                                                    │
│  ├─ Assessment Engine                                              │
│  ├─ Learner Model                                                  │
│  ├─ Policy Engine                                                  │
│  ├─ Activity Service                                               │
│  └─ Evidence/Evaluation                                            │
│                                                                     │
│ AI Orchestration                                                    │
│  ├─ Tutor/Coach                                                     │
│  ├─ Content/Activity Generator                                      │
│  └─ Evaluator                                                       │
│                                                                     │
│ Event Service | Highlight/Annotation Service | Pack Loader          │
│ Lab Orchestrator | Tool Adapters                                    │
└───────────────┬───────────────────────────┬─────────────────────────┘
                │                           │
          PostgreSQL                  Docker sandbox
      state + append events       disposable lab instances
```

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
Learner-state interfaces and initial mastery model.

### `core/policy`
Next-best-activity selection.

### `core/activity`
Lifecycle of assessment/practice activities.

### `core/evidence`
Evidence schema, aggregation and learner-model update contracts.

### `core/events`
Append-only learning event schema and persistence.

### `core/packs`
Subject-pack loading, validation and versioning.

### `ai/`
Provider-neutral LLM interface and role-specific prompts.
Do not let provider SDK types leak into domain logic.

### `labs/`
Environment lifecycle and tool adapters.

### `apps/web`
Learner UI.

## Persistence

### PostgreSQL is both:
1. transactional application database,
2. MVP event store.

Use an append-only `learning_events` table.
Derived current state lives in normalized tables for fast reads.

Do not require a separate event-stream system for MVP.

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
