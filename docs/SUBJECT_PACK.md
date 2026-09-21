# Subject Pack Specification

## Objective
A subject pack defines domain knowledge and practical training without modifying the adaptive-learning core.

A pack is pure data: it contains no code (ADR-0001, ADR-0002). In addition to domain knowledge, a pack declares everything that is tuned for its content:
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
        ├── golden/     labeled action logs -> expected evidence
        ├── learners/   simulated learner definitions
        └── sessions/   exported recorded sessions for replay
```

The dummy non-software pack used for AC-G3 is not placed in `contents/`; it lives in the harness test fixtures (ADR-0006).

## `manifest.yaml`
```yaml
id: software-engineering
version: 0.1.0
name: Software Engineering Fundamentals
default_target: web-service-troubleshooter-foundation

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
Related: ADR-0003.

The SDK provides the UI components (terminal, editor, visualization renderer, chat, side pane, etc.). The pack declares, in YAML/JSON under `ux/`, their placement, the modes, the transitions and the learner-facing wording. A pack cannot add a new UI component; new components are added to the SDK.

The three-pane layout in `docs/UX.md` is the Software Engineering pack's declaration, not a harness-wide fixed layout. Persistence of highlight/chat/pane state, the abstraction → reality mechanism, accessibility requirements and event emission for every UI operation stay fixed in the harness.

Illustrative only; the layout spec schema is not yet finalized.

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
Related: ADR-0004, ADR-0002.

The harness provides learner model, policy and assessment strategy implementations through a registry. The pack declares, under `algorithm/`, the implementation name and all parameters for each. The BKT-inspired model and the priority formula in `docs/LEARNING_LOOP.md` are one registry implementation each. A new algorithm is added to the harness, never shipped inside a pack.

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
Related: ADR-0005.

Evaluation assets belong to per-pack tuning, so they live inside the pack under `eval/`:
- `eval/golden/`: labeled action logs paired with the evidence they are expected to produce.
- `eval/learners/`: simulated learner definitions. Used to measure the learning efficiency of an algorithm configuration.
- `eval/sessions/`: exported recorded sessions. Used to recompute a recorded session under another pack version (replay).

Simulated learners cannot measure UX; UX changes can only be evaluated with metrics from real sessions.

## Versioning and tuning
Related: ADR-0002, ADR-0005.

Tuning means editing the pack and raising its `version` in `manifest.yaml`. Every event records `pack_version` (`docs/EVENT_SCHEMA.md`), and comparing pack versions is how a tuning change is evaluated. There is no separate versioning scheme for configuration. The tuning loop is run by a human: the harness reports and compares, a human edits the pack.

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
The pack may declare tool requirements, but implementation lives in tool adapters.

Software Engineering adapters:
- TerminalAdapter
- EditorAdapter
- BrowserAdapter
- DatabaseAdapter
- PacketCaptureAdapter (later)

Future English adapters:
- MicrophoneAdapter
- ASRAdapter
- TTSAdapter
- PronunciationEvaluator
