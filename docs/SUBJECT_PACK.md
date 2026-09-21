# Subject Pack Specification

## Objective
A subject pack defines domain knowledge and practical training without modifying the adaptive-learning core.

## Suggested structure

```text
packs/
└── software-engineering/
    ├── manifest.yaml
    ├── targets/
    ├── skills/
    ├── activities/
    ├── environments/
    ├── evaluators/
    ├── visualizations/
    ├── references/
    └── prompts/
```

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

## Skill definition
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
