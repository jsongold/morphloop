# UX Specification

## Harness fixed vs pack declared
UX is decided per subject pack through a declarative layout spec (ADR-0003). A pack is pure data and ships no UI code.

Harness fixed (applies to every pack):
- the UI components provided by the SDK: terminal, editor, browser, visualization renderer, side pane, chat, etc. (ADR-0001). A new component is added to the SDK, never to a pack,
- persistence of highlight, chat and pane state,
- the abstraction → reality interaction mechanism,
- every UI operation is emitted as an event,
- the accessibility requirements.

Pack declared (YAML/JSON):
- component placement,
- main-pane modes,
- transitions,
- wording.

The concrete layout spec schema is not yet decided (ADR-0003).

## Primary layout (Software Engineering pack reference layout)
This three-pane layout is not a fixed harness specification. It is the UX declaration of the Software Engineering pack, kept here as the reference layout (ADR-0003).
Packs for other domains may declare a different layout (e.g. an audio-first main pane).

```text
┌──────────────────────────────────────────────┬──────────────────────┐
│ MAIN PANE                                    │ SIDE PANE            │
│                                              │                      │
│ Mission / Diagram / Animation                │ Selected concept     │
│                                              │ Explanation          │
│ Practice environment                         │ Real mechanism       │
│  - Terminal                                  │ Observable artifacts │
│  - Editor                                    │ Commands             │
│  - Browser                                   │ Related concepts     │
│  - DB / Observability view                   │ References           │
│                                              │                      │
├──────────────────────────────────────────────┴──────────────────────┤
│ PERSISTENT AI CHAT                                                  │
│ selected/highlighted text is quoted automatically                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Main pane is practice-first
This is a product principle and applies to every pack, whatever layout it declares.

The default screen for a practical activity is the environment.

Do not make a long lesson article the main pane.

Main-pane modes are declared by the pack (ADR-0003). The Software Engineering pack's modes, as an example:
- Mission
- Visualize
- Practice
- Observe
- Explain/review

Transitions should preserve context and environment state.

## Abstraction → reality interaction
For a diagram/animation step, the learner must be able to choose:
- **What does this mean?**
- **How is it implemented?**
- **Show me in the real system**
- **Let me inspect it**
- **Let me change/break/fix it**

Example:
`TCP SYN` animation step → click "Inspect" → terminal/packet view opens with the relevant filter.

## Side pane
Clicking or highlighting a term opens context without leaving the activity.

Required sections:
1. concise definition,
2. why it exists,
3. mechanism,
4. concrete implementation,
5. how to observe,
6. useful commands/tools,
7. related skills,
8. optional deep reference.

Side-pane behavior itself is persisted.

## Highlight interaction
Text or concept selection must support:
- explain selection,
- ask AI,
- save as note/highlight,
- show real mechanism,
- find related concepts.

When "Ask AI" is used, the chat composer displays a visible quoted selection.

## AI chat
Always available.

Context priority:
1. explicit highlighted quote,
2. current mission,
3. current concept/view,
4. recent relevant actions,
5. learner-state summary.

The assistant must distinguish:
- hint,
- explanation,
- solution.
Default to hints/diagnosis during active missions.

## Visualization style
Goal: reveal mechanism, not produce cinematic content.

Use:
- sequence diagrams,
- state transitions,
- animated packets,
- moving requests,
- B-tree traversal,
- lock/wait relationships,
- process/socket lifecycle,
- query-plan execution flow.

A 10-second mechanically accurate animation is more valuable than a polished 10-minute video.

## Tuning and evaluating UX
UX is tuned by editing the pack's layout spec and raising the pack version; there is no harness-wide UX parameter or default to adjust (ADR-0002).
Pack versions are compared through the `pack_version` recorded on events.
UX can only be evaluated with metrics from real sessions. Synthetic learners cannot measure UX (ADR-0005).

## Persistence
On reload/session resume restore:
- current activity,
- lab state if still available,
- pane state,
- open concept,
- highlights,
- chat,
- timeline,
- learner state,
- selected visualization step where feasible.

## Accessibility
- Keyboard-first terminal/editor operation
- Selection/highlight must work without mouse-only interaction
- Diagrams need text equivalents
- Animation needs pause/step controls
