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
