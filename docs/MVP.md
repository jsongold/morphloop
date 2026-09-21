# MVP v0.1

## Goal
Prove that the generic adaptive loop works with real Software Engineering practice.

Do NOT attempt to build the complete curriculum.

The MVP proves the loop with a pair: this repository (the Harness/SDK) and the `contents/software-engineering/` subject pack (ADR-0001).
The harness provides the engine, UI components and algorithm registry; the pack declares the UX layout and the algorithm configuration with all parameters (ADR-0002, ADR-0003, ADR-0004).

The v0.1 gate is a single DNS vertical slice (ADR-0012).
The design direction of ADR-0001 to ADR-0011 is kept. Only the implementation order and the per-version gates change.

## Scope by version (ADR-0012)

### v0.1: DNS vertical slice
A learner can go through:

load the pack from a path → start a DNS `ActivityAttempt` → start an isolated lab → run diagnostic commands in the in-browser terminal → commands and output are persisted as events → recover the service and submit → deterministic checks and, where needed, the LLM evaluator emit structured evidence → DNS mastery is updated with evidence ID references → view the timeline → highlight a DNS concept and ask the AI with the quote attached → reload restores session, chat and highlights.

Also in v0.1:
- the DNS visualization and reality mapping (AC-C1 to AC-C3),
- the contracts that are expensive to retrofit, kept from the first slice: definition vs instance (ADR-0007), the event contract (ADR-0008), the core / domain adapter / pack boundary (ADR-0009), provenance recording (ADR-0010),
- AC-G1 and AC-G2.

Relaxed in v0.1:
- UX layout: built as SDK UI components, with the SE pack layout held as pack data. The generic layout spec schema is not fixed; it will be extracted from the first implementation (ADR-0003 as revised by ADR-0012).
- Algorithms: the registry mechanism and loading of the pack declaration are built, but one learner model implementation is enough. Policy and assessment strategy are v0.2.

### v0.2 or later
- HTTP lab and DB indexing lab (the three skills of ADR-0006 become the v0.2 gate),
- adaptive diagnostic and policy-based activity selection (AC-A1, AC-A2, AC-A4),
- DB visualization (AC-C4),
- generalization of the layout spec (AC-H1),
- `simulate` / `replay` / `report` (AC-I group),
- holdout tasks (ADR-0011),
- dummy second pack (AC-G3).

The per-criterion gate is listed in `docs/ACCEPTANCE_CRITERIA.md`.

## Learning scope
Initial pack: three skills, each backed by a practical lab (ADR-0006).
Only DNS resolution is in the v0.1 gate; HTTP and DB indexing are gated at v0.2 or later (ADR-0012).

### Networking/Web
- DNS resolution
- HTTP request/response lifecycle

### Database
- basic indexing and `EXPLAIN` / query plan interpretation

TCP is deferred beyond the MVP (ADR-0006); see Out of scope.

Three lab-backed skills let AC-A1 (v0.2 or later) be met without replacing practice with quizzes. This is enough to test multiple practical environments and abstraction→reality mappings.

## Required user journey

Each step shows its gate version (ADR-0012). In v0.1 the journey runs on the DNS activity only: the learner starts the DNS activity directly, without the diagnostic and policy selection of steps 1 to 4.

### 1. Start target (v0.2 or later)
Learner chooses:
`Web Service Troubleshooting Foundations`

### 2. Diagnostic assessment (v0.2 or later)
System runs short practical tasks across target skills.

### 3. Gap detection (v0.2 or later)
System produces an initial learner map.

### 4. Adaptive mission (v0.2 or later)
Policy chooses a weak skill and starts a practical mission.

### 5. Practical interaction (v0.1: browser terminal; database: v0.2 or later)
Learner uses browser terminal and/or database.

All commands/actions are captured.

### 6. Contextual help (v0.1)
Learner can:
- click/highlight a term,
- get side-pane explanation,
- ask AI about the selection with the quote attached,
- inspect an abstract concept in the real environment.

### 7. Evaluation (v0.1)
System evaluates:
- final outcome,
- investigation process,
- hints,
- command sequence,
- conceptual explanation where relevant.

### 8. Learner-model update (v0.1)
Mastery changes with evidence references.

### 9. Persistence (v0.1)
Reload/resume restores the learning session, highlights and chat.

### 10. Next activity (v0.2 or later)
Policy chooses a different next activity based on updated state.

## Required lab fixtures

### Lab A: broken DNS resolver (v0.1)
Learner must diagnose name-resolution failure.

Useful tools:
- `dig`
- `cat /etc/resolv.conf`
- `getent hosts`
- `curl`
- optional `tcpdump`

### Lab B: slow indexed lookup (v0.2 or later)
A PostgreSQL query is slow because an appropriate index is absent or ineffective.

Useful tools:
- `psql`
- `EXPLAIN (ANALYZE, BUFFERS)`
- schema/index inspection
- `CREATE INDEX`

### Lab C: HTTP request/response lifecycle (v0.2 or later)
A practical lab in which the learner diagnoses a failure in the HTTP request/response lifecycle (ADR-0006).
The concrete fixture is not yet decided.

Candidate useful tools (not final):
- `curl -v`
- response status/headers inspection
- server log inspection

## Required visualization examples
1. (v0.1, AC-C1 to AC-C3) DNS resolution sequence with links to:
   - `/etc/resolv.conf`
   - DNS packet observation
   - `dig`

2. (v0.2 or later, AC-C4) B-tree/index lookup animation with links to:
   - query plan,
   - index metadata,
   - real query execution.

## Out of scope
- TCP skills and TCP lab (deferred, ADR-0006)
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
