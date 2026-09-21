# Domain Model

## Core entities

Entities fall into two named groups (ADR-0007):
- Definitions: versioned data inside a pack. Identified by a pack-internal id (e.g. `diagnose-dns-resolver-failure-v1`); fully qualified as `pack_id` + `pack_version` + definition id.
- Runtime instances: execution records in the database. Each instance references the definition id it came from and the `pack_version`.

The pack is the source of truth for definitions. Core code treats definitions as read-only data obtained through the pack loader.

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

## Definitions (versioned data in the pack)

### SkillDefinition
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

`id` is the `skill_id`. It is stable across pack versions (see invariants).

### TargetProfile
A set of desired skills and mastery thresholds.

### AssessmentDefinition
A diagnostic activity or sequence intended to reduce uncertainty about skill state.

### ActivityDefinition
A concrete learner task as defined in the pack.

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

### EnvironmentDefinition
The fixture definition of a disposable runtime used by an activity.

### EvaluatorDefinition
The rubric used to judge an attempt. Checks referenced by name are implemented in the domain adapter (ADR-0009).

### VisualizationDefinition
A declarative explanation of an abstract mechanism.
May have mappings to real observations.

## Runtime instances (execution records in the database)

| Instance | ID prefix | Started from |
|---|---|---|
| LearningSession | `ses_` | - |
| AssessmentRun | `asr_` | AssessmentDefinition |
| ActivityAttempt | `att_` | ActivityDefinition |
| LabInstance | `lab_` | EnvironmentDefinition |
| Evaluation | `evl_` | EvaluatorDefinition |
| Evidence | `ev_` | - |

### LearningSession
A resumable period of interaction.

### AssessmentRun
One execution of an AssessmentDefinition.

### ActivityAttempt
One execution of an ActivityDefinition by a learner. Retries and other sessions create new attempts of the same definition.

### LabInstance
One running disposable environment for an attempt. A lab reset keeps the same ActivityAttempt and creates a new LabInstance.

### Evaluation
A judgment over one or more actions that produces evidence. Belongs to exactly one ActivityAttempt.

### Evidence
A normalized signal supporting or weakening a mastery inference.

## Other entities

### Learner
Identity only; avoid embedding derived learning state directly.

### LearnerSkillState
A derived projection, not a source of truth: the current estimate per learner/skill, rebuilt from events (ADR-0008). Identified by `learner_id` + `pack_id` + `skill_id` (ADR-0007).

```text
learner_id
pack_id
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

### Action
A learner or system action observed during an attempt. Recorded as events (`docs/EVENT_SCHEMA.md`).

### Highlight
A persistent learner selection/annotation anchored to content. Runtime data; its current state is a projection of events (ADR-0008).

### ConversationThread / Message
Persistent AI conversation. Runtime data; its current state is a projection of events (ADR-0008).
Messages may reference highlights and event IDs.

### ObservableArtifact
Something visible in a real system, observed at runtime inside a LabInstance. A VisualizationDefinition may map to it.
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

Updated per ADR-0007.

```text
Subject 1─* SubjectPack
SubjectPack 1─* SkillDefinition
SubjectPack 1─* ActivityDefinition / EnvironmentDefinition / AssessmentDefinition
               / EvaluatorDefinition / VisualizationDefinition / TargetProfile
SkillDefinition *─* SkillDefinition (prerequisites)

Learner 1─* LearnerSkillState
Learner 1─* LearningSession

LearningSession 1─* AssessmentRun
LearningSession 1─* ActivityAttempt
AssessmentDefinition 1─* AssessmentRun
ActivityDefinition 1─* ActivityAttempt   (retries, other sessions)
ActivityAttempt 1─* LabInstance          (reset = same attempt, new LabInstance)
EnvironmentDefinition 1─* LabInstance
ActivityAttempt 1─* Event
ActivityAttempt 1─* Evaluation
Evaluation 1─* Evidence
Evidence *─1 SkillDefinition (by skill_id)

LearningSession 1─* Highlight
LearningSession 1─* Message
Message *─* Highlight

Every runtime instance ─> definition id + pack_version
```

## Important invariants
- LearnerSkillState is derived; evidence/events are source of truth.
- Projections can be rebuilt from events, and the rebuilt state matches the live state; this is verified by tests (ADR-0008).
- No mastery update without evidence reference.
- Highlights remain valid across reloads and should include content version.
- Subject packs are versioned. A historical session must point to the pack version it used. Every runtime instance references its definition id and `pack_version` (ADR-0007).
- `skill_id` is stable across pack versions. A change in a skill's meaning gets a new `skill_id`; old ids are never reused (ADR-0007).
- LearnerSkillState is identified by learner + `pack_id` + `skill_id` (ADR-0007).
- The pack is the source of truth for definitions. The database does not hold a copy of a definition as the source of truth; a reference cache is allowed (ADR-0007).
- Generic core entities cannot import software-engineering-specific types. The extension boundary has three layers (ADR-0009): core (domain-agnostic), domain adapter (domain-specific code such as checks, fixture providers and tool adapters; depends only on core interfaces), and pack (data). Core imports neither a domain adapter nor a pack.
