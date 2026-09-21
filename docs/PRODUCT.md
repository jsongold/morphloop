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
