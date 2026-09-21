# Adaptive Learning Loop

## Harness fixed vs pack declared
Learning algorithms are provided by a harness registry and selected by the pack (ADR-0004).

Harness fixed (applies to every pack):
- the skeleton of the canonical loop,
- the structure of evidence and the event schema,
- the four conditions in "8. Update and repeat": reference evidence IDs, be persisted, emit an event, be reproducible,
- the "Critical rule": correct final state ≠ mastery,
- schema validation of LLM outputs,
- the event contract: ordering by `position`, atomic event append + projection update, idempotency keys, causation/correlation IDs, DB-enforced append-only, rebuild from events (ADR-0008),
- the separation of pack definitions (e.g. `ActivityDefinition`) from runtime instances (e.g. `ActivityAttempt`); every instance references its definition id and `pack_version` (ADR-0007),
- provenance recorded on events: harness version, `pack_id` / `pack_version` / pack content hash, registry implementation `name@version`, and the other items listed in ADR-0010.

Pack declared:
- which learner model, policy and assessment strategy implementation to use, by registry name,
- every parameter of those implementations. The harness holds no default values (ADR-0002).

A pack is pure data and ships no code. A new algorithm is added to the harness registry, and registry implementations must not depend on a specific subject (ADR-0004).
Domain-agnostic implementations (learner model, policy, assessment) live in the core registry; a domain-specific algorithm, if one is ever needed, lives in a domain adapter (ADR-0009).
v0.1 builds the registry mechanism and the pack declaration loading but needs only one learner model implementation; policy and assessment strategy implementations come in v0.2 or later (ADR-0012).
The registry interface and the pack-side declaration schema are not yet decided (ADR-0004).

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

Example (the MVP target; TCP skills are deferred, ADR-0006):
```yaml
target:
  id: web-service-troubleshooter-foundation
  required_mastery:
    network.dns.resolution: 0.80
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
That model is one implementation in the harness registry, not the harness's learner model (ADR-0004). The pack declares which implementation it uses and all of its parameters (ADR-0002).
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

Initial policy implementation (one implementation in the harness registry, ADR-0004):
`priority = gap × uncertainty × target_weight × prerequisite_readiness × novelty_adjustment`

Its weights and other parameters are declared by the pack; the harness holds no defaults (ADR-0002).

Keep this policy replaceable.

## 5. Activity generation
What is defined here is an `ActivityDefinition` (versioned pack data); each execution of it is an `ActivityAttempt`, and one definition can have many attempts (ADR-0007).

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
  "attempt_id": "att_123",
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

## 9. Tuning and evaluation
Tuning means optimizing a pack's UX and learning algorithm, and is done by raising the pack version (ADR-0002).
Versions are compared using the provenance recorded on events, not `pack_version` alone: `pack_version` is a human-facing label and the pack content hash is the source of identity. A comparison report shows the diff between the two pack contents per top-level directory (`algorithm/`, `ux/`, `activities/`, `evaluators/`, ...) so that it is readable what was changed (ADR-0010).

### Primary metric (ADR-0011)
Learner time and number of activities until the learner passes the held-out transfer tasks in the pack's `eval/holdout/`.

"Reaching target mastery" is not the primary metric; it is internal state of the learner model.
Reason: mastery is an estimate made by the pack's own learner model, and the rubric, model parameters and thresholds all belong to the pack (ADR-0002). A configuration that raises the estimate faster, or a laxer rubric or threshold, would look "more efficient". That is circular self-grading, so the metric is anchored outside the learner model.

Holdout rules:
- never presented in practice or diagnostic assessment,
- uses fixtures the learner has not seen (unknown faults), different from the practice activities,
- scored by deterministic checks only; no LLM evaluator and no learner-model estimate,
- frozen during a tuning comparison period; if the holdout changes, results from before the change are not compared. Identity is checked with the content-hash approach of ADR-0010 (hash scope not yet decided),
- a holdout becomes known to the learner once taken, so each skill needs several holdouts and used ones are recorded. The number and the recording method are not yet decided.

### Secondary metrics (ADR-0011)
- prediction accuracy and calibration: compare the learner model's predicted probability of success on the next task with the actual outcome. Holdout pass/fail is also a prediction target. True mastery is not observable, so it is not measured directly,
- hint dependency,
- time to first effective action.

Delayed retest (retention) is not adopted now; it remains a candidate secondary metric.

### What each means can and cannot measure (ADR-0011)
| Means | Can measure | Cannot measure | Notes |
|---|---|---|---|
| `simulate` (synthetic learners) | whether an algorithm configuration works as implemented, parameter sensitivity, policy comparison within the simulator's assumptions | UX, real learning effect | the generative model of the synthetic learners must be defined with assumptions different from the learner model's; shared assumptions mean overfitting to the simulator |
| `replay` | how estimates evolve and how prediction accuracy changes when the model configuration changes | policy effect, UX | Level 1 only, see below (ADR-0010) |
| real sessions | the primary metric, UX metrics and all secondary metrics | version comparison on the same skill (for now) | see "Single real learner" below |
| golden set (`eval/golden/`) | evaluator regression: signal direction (positive/negative) and relative ordering | exact numeric `strength` equality is not checked | labels are assigned by a human; labelling criteria not yet decided |

`replay` scope (ADR-0010):
- Level 1 (the level the MVP design provides): recompute the learner model from stored evidence, changing the algorithm implementation or parameters and comparing the learner-state trajectory. Deterministic.
- Level 2 (re-evaluating deterministic checks against stored events to rebuild evidence) is post-MVP.
- LLM outputs are never re-run in replay; the stored outputs are used. Re-evaluating with a different LLM evaluator is recorded as a new evaluation, distinct from the original.
- Out of scope: policy counterfactuals (what a different policy would have presented) and UX differences. These can only be evaluated with `simulate` or real sessions.
- Across versions whose `skill_id` set or activity definitions differ, Level 1 runs only on the shared `skill_id`s; unmatched evidence is kept but ignored.
- After a pack update, learner state is recomputed from stored evidence under the new configuration, through the same path as Level 1.

Single real learner (ADR-0011): the only real learner at present is the owner. The same person cannot learn the same skill twice, so versions cannot be compared on the same skill. For now UX tuning is a subjective judgement informed by the metrics, and no statistical claim about UX is made until there are more learners or comparison across different skills becomes possible.

### Commands and assets
The harness provides three kinds of evaluation command. Names are provisional and the exact CLI shape is not yet decided:
- `simulate`: run an algorithm configuration against synthetic learners,
- `replay`: Level 1 recomputation as described above,
- `report`: summarize the above per pack, always stating which means each number came from (ADR-0011).

Evaluation assets live in the pack's `eval/` directory (`holdout/`, `golden/`, `learners/`, `sessions/`).
The evaluation commands and the holdout are gated at v0.2 or later, not v0.1 (ADR-0012).

The tuning loop is run by a human: the harness produces per-pack reports and pack-version comparisons, and a human edits the pack. There is no agent-proposed pack change and no automatic parameter search (ADR-0005).

## Critical rule
Never equate "correct final state" with mastery.

The system must distinguish:
- accidental success,
- success after strong hints,
- systematic diagnosis,
- transfer to a new scenario,
- explanation without operational competence,
- operational competence without conceptual understanding.
