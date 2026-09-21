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
