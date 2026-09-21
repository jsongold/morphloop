# MVP Acceptance Criteria

Each criterion carries a gate version (ADR-0012). The v0.1 gate is the DNS vertical slice; everything else is gated at v0.2 or later.
AC IDs are stable. The design direction of ADR-0001 to ADR-0011 is unchanged; only the implementation order and the per-version gates changed.

## A. Adaptive loop

### AC-A1 Diagnostic
Gate: v0.2 or later (ADR-0012)
Given a new learner,
when the diagnostic completes,
then at least 3 target skills have mastery estimates and uncertainty values.

### AC-A2 Weakness selection
Gate: v0.2 or later (ADR-0012)
Given unequal skill estimates,
when requesting the next activity,
then the policy chooses an activity whose primary skill is a justified learning priority.

### AC-A3 Evidence-based update
Gate: v0.1 (ADR-0012)
Given a completed activity,
when evaluation finishes,
then learner-skill state changes only through persisted evidence IDs.

### AC-A4 Adaptation
Gate: v0.2 or later (ADR-0012)
Given that a learner demonstrates mastery in the previously weak skill,
when the next activity is selected,
then the next focus changes or difficulty increases.

## B. Practical environment

### AC-B1 Terminal
Gate: v0.1 (ADR-0012)
Learner can open an in-browser terminal and execute commands in an isolated disposable lab.

### AC-B2 Persistence of actions
Gate: v0.1 (ADR-0012)
Every terminal command is persisted with timestamp, activity and session IDs.
Note: "activity ID" means the `ActivityAttempt` id. The event carries `attempt_id` and `activity_definition_id` (ADR-0007).

### AC-B3 Output
Gate: v0.1 (ADR-0012)
Terminal output is available to the evaluator and can be referenced from the timeline.

### AC-B4 Reset
Gate: v0.1 (ADR-0012)
Learner can reset the lab to a known fixture.

### AC-B5 Isolation
Gate: v0.1 (ADR-0012)
Learner environment cannot access the host filesystem or Docker socket.

## C. Abstraction → reality

### AC-C1 Visualization
Gate: v0.1 (ADR-0012)
DNS has an interactive sequence visualization.

### AC-C2 Reality mapping
Gate: v0.1 (ADR-0012)
Selecting a DNS visualization step exposes a concrete mechanism and at least one executable observation command.

### AC-C3 Real observation
Gate: v0.1 (ADR-0012)
Learner can execute the suggested command and see actual environment output without leaving the activity.

### AC-C4 Database mapping
Gate: v0.2 or later (ADR-0012)
Index visualization links to a real `EXPLAIN` / query-plan observation.

## D. Side pane / highlight

### AC-D1 Highlight
Gate: v0.1 (ADR-0012)
Learner can highlight/select a supported text/concept and open a persistent side-pane explanation.

### AC-D2 Persistence
Gate: v0.1 (ADR-0012)
Reloading restores stored highlights.

### AC-D3 Quote to AI
Gate: v0.1 (ADR-0012)
"Ask AI" from a highlight creates a chat message containing an explicit reference to the highlight.

### AC-D4 Visible citation
Gate: v0.1 (ADR-0012)
The chat UI visibly renders the referenced quote.

### AC-D5 Context correctness
Gate: v0.1 (ADR-0012)
The backend AI request includes the referenced highlight plus current activity context.

## E. AI companion

### AC-E1 Mission awareness
Gate: v0.1 (ADR-0012)
AI can identify the active mission without learner re-explaining it.

### AC-E2 Recent action awareness
Gate: v0.1 (ADR-0012)
AI can use recent terminal actions relevant to the question.

### AC-E3 Hint mode
Gate: v0.1 (ADR-0012)
During an unfinished mission, default response behavior avoids immediately giving the full solution.

### AC-E4 Structured evaluator
Gate: v0.1 (ADR-0012)
Evaluator outputs schema-valid evidence objects.

## F. Persistence / event timeline

### AC-F1 Append only
Gate: v0.1 (ADR-0012)
Historical learning events are not mutated.

### AC-F2 Reconstruction
Gate: v0.1 (ADR-0012)
A session timeline can be retrieved in chronological order.
Note: order is defined by `position`, not by `occurred_at` (ADR-0008).

### AC-F3 Learner update audit
Gate: v0.1 (ADR-0012)
Every `learner_skill.updated` event references the evidence that caused it.

### AC-F4 Resume
Gate: v0.1 (ADR-0012)
After application restart, learner can resume session state including activity, chat and highlights.

### AC-F5 Idempotent resend
Gate: v0.1 (ADR-0012)
Resending an event with the same `idempotency_key` does not create a new event (ADR-0008).

### AC-F6 Rebuild equivalence
Gate: v0.1 (ADR-0012)
Discarding the projections and rebuilding them from events yields the same state as the running system (ADR-0008).

### AC-F7 Position ordering
Gate: v0.1 (ADR-0012)
Timeline order is determined by the DB-assigned `position` (ADR-0008).

## G. Domain independence

### AC-G1 Generic core
Gate: v0.1 (ADR-0012)
Core adaptive modules do not import software-engineering pack code.
Note: the target is the core. The check verifies that the core imports neither a domain adapter nor a pack (ADR-0009).

### AC-G2 Pack loading
Gate: v0.1 (ADR-0012)
Software Engineering skills/activities load through the subject-pack interface.

### AC-G3 Dummy second pack
Gate: v0.2 or later (ADR-0012)
A minimal test pack with a non-software skill can load and run through the same assessment/activity/evidence interfaces.
This is a structural test only; no English product UI is required.
The dummy pack lives in the harness test fixtures, not under `contents/` (ADR-0006).

## H. Pack-declared configuration

### AC-H1 Declarative layout
Gate: v0.2 or later (ADR-0012)
The UX layout is rendered from the pack's declarative layout spec; the harness contains no hard-coded Software Engineering layout (ADR-0003).

### AC-H2 Pack-declared algorithms
Gate: v0.1 for the learner model only; policy and assessment strategy are gated at v0.2 or later (ADR-0012)
The learner model, policy and assessment strategy implementations and all their parameters are read from the pack declaration; the harness holds no default values (ADR-0002, ADR-0004).
Note: handling of a missing required parameter is not yet decided (ADR-0002, ADR-0004).

### AC-H3 Provenance on events
Gate: v0.1 (ADR-0012)
Events record provenance: `pack_id`, `pack_version`, the pack content hash, the `name@version` of the registry implementations used, and the other items listed in ADR-0010.
When the pack content changes, its events are identifiable as coming from a different pack even if the `pack_version` label was not bumped (ADR-0010).
Note: which event carries which provenance item is not yet decided (ADR-0010).

## I. Evaluation and tuning

Command names (`simulate`, `replay`, `report`) are provisional; the exact CLI shape is not yet decided (ADR-0005).

### AC-I1 Simulation
Gate: v0.2 or later (ADR-0012)
A simulation with synthetic learners runs the pack's algorithm configuration and reports the efficiency metric as learner time and number of activities, not activities alone, within the simulator's assumptions (ADR-0005, ADR-0011).
Note: simulation verifies that the algorithm configuration behaves correctly and shows its sensitivity to parameters. It is not evidence of learning effectiveness (ADR-0011).

### AC-I2 Replay
Gate: v0.2 or later (ADR-0012)
Replay Level 1: the learner model is recomputed from stored evidence, and the resulting learner-state trajectories are compared across configurations (ADR-0010).
Note: replay does not re-run LLM calls and does not cover policy counterfactuals or UX differences (ADR-0010).

### AC-I3 Report
Gate: v0.2 or later (ADR-0012)
A per-pack report outputs the primary metric (learner time and number of activities until the learner passes a holdout task), the secondary metrics (prediction accuracy and calibration, hint dependence, time to first useful action), and shows which means (`simulate`, `replay`, real sessions, golden set) each number came from (ADR-0011).

### AC-I4 Holdout
Gate: v0.2 or later (ADR-0012)
Holdout tasks are never served during practice or diagnostics, and they are scored by deterministic checks only (ADR-0011).
Note: the number of holdout tasks per skill and how used holdouts are recorded are not yet decided (ADR-0011).

## Completion per version (ADR-0012)

### v0.1 is complete only when
A user can:

`pack loaded from a path → DNS activity attempt → isolated lab → in-browser terminal commands → command/output events persisted → service recovered and submitted → structured evidence → DNS mastery update referencing evidence IDs → timeline → DNS visualization and reality mapping → highlight and quoted AI question → reload restores session, chat and highlights`

with the entire chain persisted and testable, and with every criterion gated at v0.1 passing.

### v0.2 or later is complete only when
A user can:

`diagnostic → weakness detected → practical mission → command execution → evaluation → mastery update → contextual highlight/chat → resume → next adaptive mission`

with the entire chain persisted and testable.
