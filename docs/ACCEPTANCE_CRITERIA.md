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
The dummy pack lives in the harness test fixtures, not under `contents/` (ADR-0006).

## H. Pack-declared configuration

### AC-H1 Declarative layout
The UX layout is rendered from the pack's declarative layout spec; the harness contains no hard-coded Software Engineering layout (ADR-0003).

### AC-H2 Pack-declared algorithms
The learner model, policy and assessment strategy implementations and all their parameters are read from the pack declaration; the harness holds no default values (ADR-0002, ADR-0004).
Note: handling of a missing required parameter is not yet decided (ADR-0002, ADR-0004).

### AC-H3 Pack version on events
Every event records `pack_version`, and changing the pack version makes its events identifiable as a different version (ADR-0002).

## I. Evaluation and tuning

Command names (`simulate`, `replay`, `report`) are provisional; the exact CLI shape is not yet decided (ADR-0005).

### AC-I1 Simulation
A simulation with synthetic learners yields a learning-efficiency metric (number of activities to reach target mastery) for the pack's algorithm configuration (ADR-0005).

### AC-I2 Replay
A recorded session can be replayed under a different pack version, recomputing learner state for comparison (ADR-0005).

### AC-I3 Report
A per-pack report outputs the primary metric and the secondary metrics (ADR-0005).

## MVP is complete only when
A user can:

`diagnostic → weakness detected → practical mission → command execution → evaluation → mastery update → contextual highlight/chat → resume → next adaptive mission`

with the entire chain persisted and testable.
