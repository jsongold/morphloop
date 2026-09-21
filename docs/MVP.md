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
