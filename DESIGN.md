# Adaptive Interactive Learning OS / Harness — Consolidated Design


---

# Product Definition

## Product name
**Adaptive Interactive Learning OS / Harness**

Working description:
> A domain-agnostic adaptive performance-training engine that diagnoses what a learner cannot yet do, generates the next highest-value practical training activity, observes real behavior, and continuously updates a persistent learner model.

## Initial product
The first subject pack is **Software Engineering Fundamentals and Troubleshooting**.

The target outcome is not "finish a software engineering course."

The target outcome is:
> The learner can understand, inspect, diagnose and repair common failures in real web-service systems across networking, web, databases, security and operating-system/infrastructure basics.

## Core problem
Traditional books, videos and courses:
- are linear,
- spend time on knowledge the learner already has,
- over-rely on passive consumption,
- poorly measure practical competence,
- separate abstractions from actual system behavior,
- forget the learner between sessions.

General-purpose AI chat improves explanation but does not provide:
- a reliable skill graph,
- diagnostic assessment,
- persistent learner state,
- practical environments,
- evidence-based progression,
- systematic reassessment.

## Product thesis
Learning should operate as a closed control loop:

`Assess → Model → Select → Practice → Observe → Evaluate → Update → Repeat`

The system should maximize improvement per unit of learner time.

## Product principles

### P1. Practice first
A learner should spend most learning time operating a system, debugging, observing, configuring, building or explaining—not reading.

### P2. Content is remediation
Long-form teaching is not the default starting point.
When the learner fails or becomes uncertain, generate the smallest explanation, diagram, animation or reference needed to unblock the next practical attempt.

### P3. Every abstraction is inspectable
For every important concept, support a path:

`Abstract model → mechanism → concrete implementation → observable artifacts → real operation`

Example:
`TCP connection → state machine → Linux TCP stack → packets/socket state → tcpdump/ss`

### P4. Everything is evidence
Useful evidence includes:
- answers,
- terminal commands and output,
- editor changes,
- browser/network requests,
- database queries,
- tool choice,
- order of investigation,
- time to first useful hypothesis,
- retries,
- hints requested,
- highlighted terms,
- questions asked,
- successful repairs,
- transfer to unfamiliar scenarios.

### P5. Everything is persistent
The product must preserve enough event history to reconstruct:
- what the learner saw,
- what they selected,
- what they asked,
- what they executed,
- what the environment returned,
- how the evaluator judged it,
- why the learner model changed.

### P6. AI is a companion, not the course
AI behavior depends on current task and learner state:
- coach,
- explainer,
- Socratic tutor,
- evaluator,
- remediation generator.

### P7. Domain-agnostic harness
Core learning logic cannot depend on software engineering.
A future English pack should reuse the same:
- skill graph,
- learner model,
- policy engine,
- activity lifecycle,
- evidence model,
- event store,
- evaluator contracts.

## Initial subject domains
Long-term Software Engineering pack:
- Networking
- Web/HTTP
- Databases
- Linux/OS
- Security
- Containers/cloud fundamentals
- Observability
- Distributed systems fundamentals

MVP scope is intentionally smaller; see `MVP.md`.

## Non-goals for MVP
- Degree/certification replacement
- Social/community features
- Instructor marketplace
- Enterprise LMS
- Long prerecorded video library
- Full IDE replacement
- Production incident access
- Autonomous changes to real user infrastructure
- Gamification system


---

# Adaptive Learning Loop

## Canonical loop

```text
Goal / Target Competency
        ↓
Skill Graph
        ↓
Diagnostic Assessment
        ↓
Evidence
        ↓
Learner Model
        ↓
Policy: choose highest-value weakness
        ↓
Activity / Mission
        ↓
Environment + Tools
        ↓
Learner actions
        ↓
Event stream
        ↓
Evaluator
        ↓
Evidence
        ↓
Learner Model update
        ↓
Reassess / next activity
        ↺
```

## 1. Target
A target is a desired competency profile, not a course.

Example:
```yaml
target:
  id: web-service-troubleshooter-foundation
  required_mastery:
    network.dns.resolution: 0.80
    network.tcp.connection: 0.80
    web.http.request_lifecycle: 0.85
    db.indexing.basics: 0.75
```

## 2. Assessment
Assessment should use the cheapest high-information evidence first.

Order of preference:
1. short practical tasks,
2. prediction/explanation tied to observed systems,
3. targeted conceptual question,
4. multiple-choice only when it is genuinely the most efficient instrument.

Assessment should adapt:
- success → harder/transfer task,
- uncertain success → nearby discriminating task,
- failure → isolate prerequisite,
- repeated misconception → targeted micro-assessment.

## 3. Learner model
Per skill, store at minimum:
- mastery probability,
- uncertainty/confidence,
- retention estimate,
- transfer ability,
- response latency summary,
- hint dependency,
- misconception tags,
- evidence count,
- last evidence timestamp,
- supporting evidence IDs.

The learner model is not the event log. It is a derived state that can be rebuilt.

### Pluggable model interface
```python
class LearnerModel:
    def update(self, state, evidence) -> LearnerState: ...
    def mastery(self, learner_id, skill_id) -> MasteryEstimate: ...
    def uncertainty(self, learner_id, skill_id) -> float: ...
```

MVP may implement a simple Bayesian/BKT-inspired model.
Do not hard-code the entire product to BKT.

## 4. Policy engine
Purpose: decide what the learner should do next.

Inputs:
- target competency,
- learner state,
- prerequisite graph,
- recent evidence,
- retention risk,
- activity history,
- available environments/tools.

Output:
```json
{
  "focus_skill": "network.tcp.retransmission",
  "supporting_skills": ["network.tcp.sequence_numbers"],
  "activity_type": "diagnose",
  "difficulty": 0.62,
  "reason": "low mastery and high uncertainty; prerequisite sufficiently mastered"
}
```

Initial priority heuristic:
`priority = gap × uncertainty × target_weight × prerequisite_readiness × novelty_adjustment`

Keep this policy replaceable.

## 5. Activity generation
An activity must define:
- skills under test,
- learner-facing mission,
- environment fixture,
- success conditions,
- observable evidence,
- allowed tools,
- evaluator/rubric,
- optional hints,
- remediation hooks.

Prefer templated/generated variants over fully free-form generation when reproducibility matters.

## 6. Observe
Capture actual behavior without requiring the learner to self-report.

Examples:
- command execution,
- command output,
- file modification,
- network request,
- SQL query,
- hint request,
- content highlight,
- concept lookup,
- AI question,
- mission reset,
- time intervals.

## 7. Evaluate
Evaluation returns structured evidence, not a prose grade.

Example:
```json
{
  "activity_id": "act_123",
  "evidence": [
    {
      "skill_id": "network.dns.resolution",
      "signal": "positive",
      "strength": 0.82,
      "dimension": "diagnose",
      "rationale": "Used dig to isolate resolver failure before changing configuration"
    },
    {
      "skill_id": "troubleshooting.hypothesis",
      "signal": "negative",
      "strength": 0.35,
      "dimension": "process",
      "rationale": "Made three unrelated configuration changes before collecting evidence"
    }
  ]
}
```

## 8. Update and repeat
Every learner-state mutation must:
- reference evidence IDs,
- be persisted,
- emit an event,
- be reproducible from source evidence.

## Critical rule
Never equate "correct final state" with mastery.

The system must distinguish:
- accidental success,
- success after strong hints,
- systematic diagnosis,
- transfer to a new scenario,
- explanation without operational competence,
- operational competence without conceptual understanding.


---

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


---

# Domain Model

## Core entities

### Subject
A domain that can be learned.
Examples:
- `software-engineering`
- `english`

### SubjectPack
A versioned definition of:
- skills,
- prerequisites,
- activities,
- environments,
- visualizations,
- references,
- evaluators.

### Skill
An atomic learnable competency.

Fields:
```text
id
subject_id
name
description
tags
prerequisite_ids
mastery_threshold
evidence_dimensions
```

### TargetProfile
A set of desired skills and mastery thresholds.

### Learner
Identity only; avoid embedding derived learning state directly.

### LearnerSkillState
Derived current estimate per learner/skill.

```text
learner_id
skill_id
mastery_probability
uncertainty
retention_score
transfer_score
hint_dependency
response_latency_summary
misconceptions[]
evidence_count
last_evidence_at
model_version
```

### LearningSession
A resumable period of interaction.

### Assessment
A diagnostic activity or sequence intended to reduce uncertainty about skill state.

### Activity
A concrete learner task.

Activity types:
- inspect
- execute
- diagnose
- repair
- build
- configure
- debug
- explain
- predict
- incident

### Environment
A disposable runtime used by an activity.

### Action
A learner or system action observed during an activity.

### Evidence
A normalized signal supporting or weakening a mastery inference.

### Evaluation
A judgment over one or more actions that produces evidence.

### Highlight
A persistent learner selection/annotation anchored to content.

### ConversationThread / Message
Persistent AI conversation.
Messages may reference highlights and event IDs.

### Visualization
A declarative explanation of an abstract mechanism.
May have mappings to real observations.

### ObservableArtifact
Something visible in a real system:
- packet,
- log,
- query plan,
- process,
- socket state,
- system call,
- metric,
- trace,
- DB row/version.

## Relationships

```text
Subject 1─* SubjectPack
SubjectPack 1─* Skill
Skill *─* Skill (prerequisites)

Learner 1─* LearnerSkillState
Learner 1─* LearningSession

LearningSession 1─* Activity
Activity 0..1─1 Environment
Activity 1─* Event
Activity 1─* Evaluation
Evaluation 1─* Evidence
Evidence *─1 Skill

LearningSession 1─* Highlight
LearningSession 1─* Message
Message *─* Highlight
```

## Important invariants
- LearnerSkillState is derived; evidence/events are source of truth.
- No mastery update without evidence reference.
- Highlights remain valid across reloads and should include content version.
- Subject packs are versioned. A historical session must point to the pack version it used.
- Generic core entities cannot import software-engineering-specific types.


---

# Event Schema and Persistence

## Goal
Persist the full learning experience as an append-only timeline.

The event log must support:
- session reconstruction,
- audit of learner-model changes,
- analytics,
- future model improvements,
- AI context retrieval,
- debugging incorrect evaluations.

## Base event envelope

```json
{
  "event_id": "evt_...",
  "event_type": "terminal.command",
  "event_version": 1,
  "occurred_at": "2026-09-21T10:00:00Z",
  "learner_id": "usr_...",
  "session_id": "ses_...",
  "activity_id": "act_...",
  "skill_ids": ["network.dns.resolution"],
  "actor": "learner",
  "payload": {},
  "metadata": {
    "pack_id": "software-engineering",
    "pack_version": "0.1.0"
  }
}
```

## Event families

### Content/navigation
- `content.opened`
- `content.closed`
- `concept.clicked`
- `content.highlighted`
- `highlight.removed`
- `sidepane.opened`
- `visualization.opened`
- `visualization.played`
- `visualization.step_selected`
- `reality_view.opened`
- `reference.opened`

### AI
- `assistant.message_requested`
- `assistant.message_generated`
- `assistant.highlight_referenced`
- `assistant.hint_requested`
- `assistant.hint_generated`

### Terminal/runtime
- `lab.started`
- `lab.reset`
- `lab.stopped`
- `terminal.command`
- `terminal.output`
- `terminal.exit`

### Editor/browser/database
- `editor.file_opened`
- `editor.changed`
- `browser.request`
- `browser.response`
- `database.query`
- `database.result`

### Assessment/activity
- `assessment.started`
- `assessment.completed`
- `activity.started`
- `activity.submitted`
- `activity.completed`
- `activity.failed`
- `evaluation.completed`
- `evidence.created`
- `learner_skill.updated`

### Session
- `session.started`
- `session.resumed`
- `session.ended`

## Terminal event example
```json
{
  "event_type": "terminal.command",
  "payload": {
    "command": "dig api.internal",
    "cwd": "/workspace",
    "terminal_id": "term_1",
    "sequence": 18
  }
}
```

Command output may be stored separately/chunked if large but must remain referenceable.

## Highlight model

A highlight must survive reload and content updates when possible.

```json
{
  "highlight_id": "hl_123",
  "content_id": "concept.network.dns",
  "content_version": "0.1.0",
  "selected_text": "recursive resolver",
  "start_offset": 148,
  "end_offset": 166,
  "semantic_anchor": "mechanism.steps[2].label",
  "context_before": "The OS forwards the query to a ",
  "context_after": " which may query authoritative servers.",
  "created_at": "..."
}
```

Prefer semantic anchors over raw DOM paths.

## AI quote/reference model
An AI user message can contain:
```json
{
  "text": "Why is this necessary?",
  "references": [
    {
      "type": "highlight",
      "id": "hl_123"
    }
  ]
}
```

The rendered UI should visibly quote the selected source.

## Learner-model update event
```json
{
  "event_type": "learner_skill.updated",
  "payload": {
    "skill_id": "network.dns.resolution",
    "previous": {"mastery_probability": 0.54},
    "next": {"mastery_probability": 0.68},
    "evidence_ids": ["ev_88", "ev_89"],
    "model_version": "bkt-lite-v1"
  }
}
```

## Storage rule
Never mutate historical learning events.
Corrections are new events.

PII/secrets from terminal output require redaction hooks before persistence.


---

# Subject Pack Specification

## Objective
A subject pack defines domain knowledge and practical training without modifying the adaptive-learning core.

## Suggested structure

```text
packs/
└── software-engineering/
    ├── manifest.yaml
    ├── targets/
    ├── skills/
    ├── activities/
    ├── environments/
    ├── evaluators/
    ├── visualizations/
    ├── references/
    └── prompts/
```

## `manifest.yaml`
```yaml
id: software-engineering
version: 0.1.0
name: Software Engineering Fundamentals
default_target: web-service-troubleshooter-foundation

tool_adapters:
  - terminal
  - editor
  - browser
  - database

content_languages:
  - en
  - ja
```

## Skill definition
```yaml
id: network.tcp.retransmission
name: TCP Retransmission
description: Diagnose and explain retransmission behavior.
prerequisites:
  - network.tcp.sequence_numbers
  - network.tcp.acknowledgement

mastery_threshold: 0.85

evidence_dimensions:
  - explain
  - predict
  - observe
  - diagnose
  - repair

observable_artifacts:
  - packet_capture
  - socket_state

tools:
  - terminal
  - tcpdump
  - ss
```

## Activity definition
```yaml
id: diagnose-dns-resolver-failure-v1
type: diagnose

skills:
  primary:
    - network.dns.resolution
  secondary:
    - troubleshooting.hypothesis

mission: |
  `api.internal` cannot be reached by name, but the service may still be running.
  Diagnose the failure and restore connectivity.

environment:
  fixture: dns-broken-resolver-v1

allowed_tools:
  - terminal

success:
  checks:
    - type: command_exit
      command: ["curl", "-fsS", "http://api.internal/health"]
      equals: 0

evaluator:
  rubric: dns-diagnosis-v1

remediation:
  concepts:
    - network.dns.resolver
    - network.dns.recursive_resolution
```

## Visualization definition

Visualizations are NOT prerecorded lectures.
They are concise models with optional animation and real-system mappings.

```yaml
id: dns-resolution-flow
type: sequence

actors:
  - browser
  - os_resolver
  - recursive_resolver
  - authoritative_dns

steps:
  - from: browser
    to: os_resolver
    label: resolve api.example.com
  - from: os_resolver
    to: recursive_resolver
    label: DNS query
    reality:
      observe:
        - command: "cat /etc/resolv.conf"
        - command: "tcpdump -ni any port 53"
  - from: recursive_resolver
    to: authoritative_dns
    label: query authoritative server
```

The UI must allow the learner to move from an abstract step to its `reality` mapping immediately.

## References
Reference content should be short by default:
- what it is,
- why it exists,
- mechanism,
- implementation example,
- how to observe,
- common failure modes.

Do not write textbook-length content unless explicitly requested.

## Evaluator definition
Prefer deterministic checks first.

```yaml
id: dns-diagnosis-v1
deterministic:
  - service_recovered
  - correct_resolver_config

semantic:
  dimensions:
    - hypothesis_quality
    - evidence_collection
    - unnecessary_changes
    - explanation_quality
```

## Domain-specific adapters
The pack may declare tool requirements, but implementation lives in tool adapters.

Software Engineering adapters:
- TerminalAdapter
- EditorAdapter
- BrowserAdapter
- DatabaseAdapter
- PacketCaptureAdapter (later)

Future English adapters:
- MicrophoneAdapter
- ASRAdapter
- TTSAdapter
- PronunciationEvaluator


---

# UX Specification

## Primary layout

```text
┌──────────────────────────────────────────────┬──────────────────────┐
│ MAIN PANE                                    │ SIDE PANE            │
│                                              │                      │
│ Mission / Diagram / Animation                │ Selected concept     │
│                                              │ Explanation          │
│ Practice environment                         │ Real mechanism       │
│  - Terminal                                  │ Observable artifacts │
│  - Editor                                    │ Commands             │
│  - Browser                                   │ Related concepts     │
│  - DB / Observability view                   │ References           │
│                                              │                      │
├──────────────────────────────────────────────┴──────────────────────┤
│ PERSISTENT AI CHAT                                                  │
│ selected/highlighted text is quoted automatically                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Main pane is practice-first
The default screen for a practical activity is the environment.

Do not make a long lesson article the main pane.

Possible main-pane modes:
- Mission
- Visualize
- Practice
- Observe
- Explain/review

Transitions should preserve context and environment state.

## Abstraction → reality interaction
For a diagram/animation step, the learner must be able to choose:
- **What does this mean?**
- **How is it implemented?**
- **Show me in the real system**
- **Let me inspect it**
- **Let me change/break/fix it**

Example:
`TCP SYN` animation step → click "Inspect" → terminal/packet view opens with the relevant filter.

## Side pane
Clicking or highlighting a term opens context without leaving the activity.

Required sections:
1. concise definition,
2. why it exists,
3. mechanism,
4. concrete implementation,
5. how to observe,
6. useful commands/tools,
7. related skills,
8. optional deep reference.

Side-pane behavior itself is persisted.

## Highlight interaction
Text or concept selection must support:
- explain selection,
- ask AI,
- save as note/highlight,
- show real mechanism,
- find related concepts.

When "Ask AI" is used, the chat composer displays a visible quoted selection.

## AI chat
Always available.

Context priority:
1. explicit highlighted quote,
2. current mission,
3. current concept/view,
4. recent relevant actions,
5. learner-state summary.

The assistant must distinguish:
- hint,
- explanation,
- solution.
Default to hints/diagnosis during active missions.

## Visualization style
Goal: reveal mechanism, not produce cinematic content.

Use:
- sequence diagrams,
- state transitions,
- animated packets,
- moving requests,
- B-tree traversal,
- lock/wait relationships,
- process/socket lifecycle,
- query-plan execution flow.

A 10-second mechanically accurate animation is more valuable than a polished 10-minute video.

## Persistence
On reload/session resume restore:
- current activity,
- lab state if still available,
- pane state,
- open concept,
- highlights,
- chat,
- timeline,
- learner state,
- selected visualization step where feasible.

## Accessibility
- Keyboard-first terminal/editor operation
- Selection/highlight must work without mouse-only interaction
- Diagrams need text equivalents
- Animation needs pause/step controls


---

# MVP v0.1

## Goal
Prove that the generic adaptive loop works with real Software Engineering practice.

Do NOT attempt to build the complete curriculum.

## Learning scope
Initial pack:
### Networking/Web
- DNS resolution
- TCP connection establishment/retransmission basics
- HTTP request/response lifecycle

### Database
- basic indexing
- `EXPLAIN` / query plan interpretation

This is enough to test multiple practical environments and abstraction→reality mappings.

## Required user journey

### 1. Start target
Learner chooses:
`Web Service Troubleshooting Foundations`

### 2. Diagnostic assessment
System runs short practical tasks across target skills.

### 3. Gap detection
System produces an initial learner map.

### 4. Adaptive mission
Policy chooses a weak skill and starts a practical mission.

### 5. Practical interaction
Learner uses browser terminal and/or database.

All commands/actions are captured.

### 6. Contextual help
Learner can:
- click/highlight a term,
- get side-pane explanation,
- ask AI about the selection with the quote attached,
- inspect an abstract concept in the real environment.

### 7. Evaluation
System evaluates:
- final outcome,
- investigation process,
- hints,
- command sequence,
- conceptual explanation where relevant.

### 8. Learner-model update
Mastery changes with evidence references.

### 9. Persistence
Reload/resume restores the learning session, highlights and chat.

### 10. Next activity
Policy chooses a different next activity based on updated state.

## Required lab fixtures

### Lab A: broken DNS resolver
Learner must diagnose name-resolution failure.

Useful tools:
- `dig`
- `cat /etc/resolv.conf`
- `getent hosts`
- `curl`
- optional `tcpdump`

### Lab B: slow indexed lookup
A PostgreSQL query is slow because an appropriate index is absent or ineffective.

Useful tools:
- `psql`
- `EXPLAIN (ANALYZE, BUFFERS)`
- schema/index inspection
- `CREATE INDEX`

## Required visualization examples
1. DNS resolution sequence with links to:
   - `/etc/resolv.conf`
   - DNS packet observation
   - `dig`

2. B-tree/index lookup animation with links to:
   - query plan,
   - index metadata,
   - real query execution.

## Out of scope
- Full security curriculum
- Full Linux curriculum
- English pack implementation
- Mobile app
- Billing
- Teams
- Multi-tenant enterprise controls
- Production Kubernetes
- Recommendation ML beyond simple policy
- Long-form video production pipeline


---

# MVP Acceptance Criteria

## A. Adaptive loop

### AC-A1 Diagnostic
Given a new learner,
when the diagnostic completes,
then at least 3 target skills have mastery estimates and uncertainty values.

### AC-A2 Weakness selection
Given unequal skill estimates,
when requesting the next activity,
then the policy chooses an activity whose primary skill is a justified learning priority.

### AC-A3 Evidence-based update
Given a completed activity,
when evaluation finishes,
then learner-skill state changes only through persisted evidence IDs.

### AC-A4 Adaptation
Given that a learner demonstrates mastery in the previously weak skill,
when the next activity is selected,
then the next focus changes or difficulty increases.

## B. Practical environment

### AC-B1 Terminal
Learner can open an in-browser terminal and execute commands in an isolated disposable lab.

### AC-B2 Persistence of actions
Every terminal command is persisted with timestamp, activity and session IDs.

### AC-B3 Output
Terminal output is available to the evaluator and can be referenced from the timeline.

### AC-B4 Reset
Learner can reset the lab to a known fixture.

### AC-B5 Isolation
Learner environment cannot access the host filesystem or Docker socket.

## C. Abstraction → reality

### AC-C1 Visualization
DNS has an interactive sequence visualization.

### AC-C2 Reality mapping
Selecting a DNS visualization step exposes a concrete mechanism and at least one executable observation command.

### AC-C3 Real observation
Learner can execute the suggested command and see actual environment output without leaving the activity.

### AC-C4 Database mapping
Index visualization links to a real `EXPLAIN` / query-plan observation.

## D. Side pane / highlight

### AC-D1 Highlight
Learner can highlight/select a supported text/concept and open a persistent side-pane explanation.

### AC-D2 Persistence
Reloading restores stored highlights.

### AC-D3 Quote to AI
"Ask AI" from a highlight creates a chat message containing an explicit reference to the highlight.

### AC-D4 Visible citation
The chat UI visibly renders the referenced quote.

### AC-D5 Context correctness
The backend AI request includes the referenced highlight plus current activity context.

## E. AI companion

### AC-E1 Mission awareness
AI can identify the active mission without learner re-explaining it.

### AC-E2 Recent action awareness
AI can use recent terminal actions relevant to the question.

### AC-E3 Hint mode
During an unfinished mission, default response behavior avoids immediately giving the full solution.

### AC-E4 Structured evaluator
Evaluator outputs schema-valid evidence objects.

## F. Persistence / event timeline

### AC-F1 Append only
Historical learning events are not mutated.

### AC-F2 Reconstruction
A session timeline can be retrieved in chronological order.

### AC-F3 Learner update audit
Every `learner_skill.updated` event references the evidence that caused it.

### AC-F4 Resume
After application restart, learner can resume session state including activity, chat and highlights.

## G. Domain independence

### AC-G1 Generic core
Core adaptive modules do not import software-engineering pack code.

### AC-G2 Pack loading
Software Engineering skills/activities load through the subject-pack interface.

### AC-G3 Dummy second pack
A minimal test pack with a non-software skill can load and run through the same assessment/activity/evidence interfaces.
This is a structural test only; no English product UI is required.

## MVP is complete only when
A user can:

`diagnostic → weakness detected → practical mission → command execution → evaluation → mastery update → contextual highlight/chat → resume → next adaptive mission`

with the entire chain persisted and testable.


---

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
