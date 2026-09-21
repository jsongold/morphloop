# Subject Pack Specification

## Objective
A subject pack defines domain knowledge and practical training without modifying the adaptive-learning core.

A pack is pure data (ADR-0001, ADR-0002), in the sense defined by ADR-0009: a pack contains no code that runs inside the harness process. It may contain definitions that are executed inside the sandbox — image definitions, command arrays, seed data, fixture settings. Those run only inside the learner sandbox; the harness never passes a pack-derived string to a shell on the host.

Everything in a pack is a Definition (ADR-0007): skill, activity, environment (fixture), evaluator (rubric), visualization and target profile are versioned data. Runtime instances (`ActivityAttempt`, `LabInstance`, `Evaluation`, `Evidence`, ...) live in the database and always reference the definition id and `pack_version` they came from. See `docs/DOMAIN_MODEL.md`.
- Every `id` in this file is a definition id, local to the pack. The fully qualified form is `pack_id` + `pack_version` + definition id.
- `skill_id` is stable across pack versions. If the meaning of a skill changes, issue a new `skill_id`; never reuse the old one.

In addition to domain knowledge, a pack declares everything that is tuned for its content:
- the UX for that content (ADR-0003),
- the algorithm configuration: which learner model, policy and assessment strategy to use (ADR-0004),
- every tuning parameter those need (ADR-0002).

The harness has no default values. It receives the path of a pack and reads it; it never imports `contents/` (ADR-0001).

## Suggested structure

Packs live in the top-level `contents/<pack-id>/` directory of this repository (ADR-0001).

```text
contents/
└── software-engineering/
    ├── manifest.yaml
    ├── ux/             layout spec
    ├── algorithm/      learner model / policy / assessment selection + all parameters
    ├── targets/
    ├── skills/
    ├── activities/
    ├── environments/
    ├── evaluators/
    ├── visualizations/
    ├── references/
    ├── prompts/
    └── eval/
        ├── holdout/    held-out transfer tasks, never used for practice or diagnosis
        ├── golden/     labeled action logs -> expected evidence
        ├── learners/   simulated learner definitions
        └── sessions/   exported recorded sessions for replay
```

The dummy non-software pack used for AC-G3 is not placed in `contents/`; it lives in the harness test fixtures (ADR-0006).

## `manifest.yaml`
Related: ADR-0009.

The manifest declares the domain adapters the pack requires and a version range for each. The pack refers to adapter capabilities (checks, fixtures, tools) by name only. `tool_adapters` lists the tools the pack uses; a tool adapter is one kind of capability provided by the domain adapter layer (see Domain-specific adapters).

At load time the harness verifies that every check, fixture and tool the pack references is registered. If any is missing, the harness refuses to load the pack.

Illustrative only; ADR-0009 does not fix the key names. `domain_adapters`, the adapter name and the range syntax below are placeholders.

```yaml
id: software-engineering
version: 0.1.0
name: Software Engineering Fundamentals
default_target: web-service-troubleshooter-foundation

domain_adapters:          # illustrative
  - name: software-engineering
    version: ">=0.1 <0.2"

tool_adapters:
  - terminal
  - editor
  - browser
  - database

content_languages:
  - en
  - ja
```

## UX declaration
Related: ADR-0003, ADR-0012.

The SDK provides the UI components (terminal, editor, visualization renderer, chat, side pane, etc.). The pack declares, in YAML/JSON under `ux/`, their placement, the modes, the transitions and the learner-facing wording. A pack cannot add a new UI component; new components are added to the SDK.

The three-pane layout in `docs/UX.md` is the Software Engineering pack's declaration, not a harness-wide fixed layout. Persistence of highlight/chat/pane state, the abstraction → reality mechanism, accessibility requirements and event emission for every UI operation stay fixed in the harness.

Illustrative only. The direction is adopted, but the generic layout spec schema is not fixed in v0.1: the UI components are built in the SDK, the Software Engineering layout is held as pack data, and the schema is extracted from the first implementation (the DNS slice) and then finalized (ADR-0012).

```yaml
# ux/layout.yaml
layout:
  main:
    modes: [mission, visualize, practice, observe, review]
    default_mode: practice
    components: [terminal, editor, browser, database]
  side:
    component: concept_pane
  bottom:
    component: ai_chat
    persistent: true
```

## Algorithm declaration
Related: ADR-0004, ADR-0002, ADR-0009, ADR-0012.

The harness provides learner model, policy and assessment strategy implementations through a registry. The pack declares, under `algorithm/`, the implementation name and all parameters for each. The BKT-inspired model and the priority formula in `docs/LEARNING_LOOP.md` are one registry implementation each. A new algorithm is never shipped inside a pack: a domain-agnostic one is added to the core registry, a domain-specific one to a domain adapter (ADR-0009).

v0.1 needs the registry mechanism, declaration loading and one learner model implementation only; policy and assessment strategy implementations are v0.2 or later (ADR-0012).

Illustrative only; the declaration schema and the registry interface are not yet finalized. Implementation names, parameter names and values below are placeholders.

```yaml
# algorithm/algorithm.yaml
learner_model:
  implementation: bkt-lite
  parameters:
    p_init: 0.2
    p_learn: 0.15
    p_slip: 0.1
    p_guess: 0.2

policy:
  implementation: priority-product
  parameters:        # weights of the priority formula in docs/LEARNING_LOOP.md
    gap: 1.0
    uncertainty: 1.0
    target_weight: 1.0
    prerequisite_readiness: 1.0
    novelty_adjustment: 1.0

assessment:
  implementation: adaptive-practical-first
  parameters:
    max_diagnostic_activities: 6
```

Because the harness has no defaults, a pack must declare every parameter it needs. How a pack with missing parameters is handled (validation details) is not yet decided.

## Evaluation assets
Related: ADR-0005, ADR-0010, ADR-0011.

Evaluation assets belong to per-pack tuning, so they live inside the pack under `eval/`.

`eval/holdout/` — held-out transfer tasks, the external anchor of the primary metric (learner time and activity count until the holdout is passed) (ADR-0011):
- never presented for practice or diagnosis;
- use fixtures different from the practice activities (unseen failures);
- scored by deterministic checks only; no LLM evaluator and no learner-model estimate;
- frozen during a tuning comparison period; if the holdout changes, results from before the change are not compared with later ones. Identity is confirmed with the content hash approach of ADR-0010;
- a holdout becomes known to a learner once taken, so a skill has several holdout tasks and used ones are recorded. The number per skill and the operating details are not yet decided.
- Holdout is a v0.2-or-later gate (ADR-0012).

`eval/golden/` — labeled action logs paired with the evidence they are expected to produce. This is the regression test for evaluators. What is verified is the direction of each signal (positive/negative) and the relative ordering, not exact `strength` values. Labels are assigned by a human; the labeling criteria are not yet decided (ADR-0011).

`eval/learners/` — simulated learner definitions, used by `simulate` to check that an algorithm configuration works, its parameter sensitivity, and policy comparison within the simulator's assumptions. The generative model of a simulated learner must be defined with assumptions different from the learner model's; sharing them overfits the configuration to the simulator (ADR-0011).

`eval/sessions/` — exported recorded sessions, the input of replay Level 1: recomputing the learner model from stored evidence under a different algorithm implementation or parameters. Replay does not cover policy counterfactuals or UX differences, and never re-runs an LLM (ADR-0010). Exported sessions need redaction; its specification is not yet decided.

Simulated learners cannot measure UX or real learning effect; UX changes can only be evaluated with metrics from real sessions.

## Versioning and tuning
Related: ADR-0002, ADR-0005, ADR-0010.

Tuning means editing the pack and raising its `version` in `manifest.yaml`. The tuning loop is run by a human: the harness reports and compares, a human edits the pack.

Pack identity (ADR-0010):
- `pack_version` is a human-facing label. The pack content hash, computed from the contents of the pack directory, is the source of truth for identity; it also detects an edit made without raising the version.
- Whether `eval/` is included in the hash is not yet decided.
- Events record `pack_id`, `pack_version` and the pack content hash together with the rest of the provenance (`docs/EVENT_SCHEMA.md`). `pack_version` alone is not enough to reproduce or compare results.

Comparison (ADR-0010):
- A comparison report shows the diff between two pack contents per top-level directory (`algorithm/`, `ux/`, `activities/`, `evaluators/`, ...), so that a result can be read as "the effect of what changed".
- There are no separate versioning schemes for tuning and for content.
- When a pack is updated, learner state is recomputed from the stored evidence under the new configuration (the same path as replay Level 1). Only `skill_id`s common to both versions are recomputed; evidence that no longer maps is kept but ignored.

## Skill definition
The TCP skill below is an example only; TCP is outside the MVP skill scope (ADR-0006).

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
The `id` is an `ActivityDefinition` id; each run of it is an `ActivityAttempt` (`att_...`) in the database (ADR-0007). `environment.fixture` names a fixture registered by a domain adapter, and the `command` array under `success.checks` is executed inside the learner sandbox, never on the host (ADR-0009).

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

The names under `deterministic` (`service_recovered`, `correct_resolver_config`) are the registered names of check implementations in a domain adapter. The pack only references them; the code is not in the pack (ADR-0009). Any `command` array a check runs, such as the one under `success.checks` in an activity definition, is executed inside the learner sandbox.

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
Related: ADR-0009.

Domain-specific code lives in the domain adapter layer, between the core and the pack. The pack declares what it requires by name (see `manifest.yaml`); the implementation lives in a domain adapter.

A domain adapter provides:
- deterministic check implementations (e.g. `service_recovered`, `correct_resolver_config`),
- environment fixture providers (the domain-specific part of starting, resetting and destroying a lab),
- tool adapters (listed below).

Rules:
- A domain adapter is a package separate from the core, in the same repository, and has its own version (recorded in provenance, ADR-0010).
- The core never imports a domain adapter. The dependency points from the adapter to the core interfaces only.
- Adapter capabilities are registered by name + version, following the same idea as the algorithm registry (ADR-0004). Whether there is one registry or one per kind is decided at implementation time.
- Adapter code runs on the host as part of the harness and is reviewed code; it is not part of a pack.
- The directory name for domain adapters is not yet decided.

Software Engineering tool adapters:
- TerminalAdapter
- EditorAdapter
- BrowserAdapter
- DatabaseAdapter
- PacketCaptureAdapter (later)

Future English tool adapters and checks:
- MicrophoneAdapter
- ASRAdapter
- TTSAdapter
- PronunciationEvaluator
