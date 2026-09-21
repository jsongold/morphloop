# Event Schema and Persistence

## Goal
Persist the full learning experience as an append-only timeline.

The event log must support:
- session reconstruction,
- audit of learner-model changes,
- analytics,
- future model improvements,
- AI context retrieval,
- debugging incorrect evaluations.

## Base event envelope

```json
{
  "event_id": "evt_...",
  "event_type": "terminal.command",
  "event_version": 1,
  "occurred_at": "2026-09-21T10:00:00Z",
  "learner_id": "usr_...",
  "session_id": "ses_...",
  "activity_id": "act_...",
  "skill_ids": ["network.dns.resolution"],
  "actor": "learner",
  "payload": {},
  "metadata": {
    "pack_id": "software-engineering",
    "pack_version": "0.1.0"
  }
}
```

## Event families

### Content/navigation
- `content.opened`
- `content.closed`
- `concept.clicked`
- `content.highlighted`
- `highlight.removed`
- `sidepane.opened`
- `visualization.opened`
- `visualization.played`
- `visualization.step_selected`
- `reality_view.opened`
- `reference.opened`

### AI
- `assistant.message_requested`
- `assistant.message_generated`
- `assistant.highlight_referenced`
- `assistant.hint_requested`
- `assistant.hint_generated`

### Terminal/runtime
- `lab.started`
- `lab.reset`
- `lab.stopped`
- `terminal.command`
- `terminal.output`
- `terminal.exit`

### Editor/browser/database
- `editor.file_opened`
- `editor.changed`
- `browser.request`
- `browser.response`
- `database.query`
- `database.result`

### Assessment/activity
- `assessment.started`
- `assessment.completed`
- `activity.started`
- `activity.submitted`
- `activity.completed`
- `activity.failed`
- `evaluation.completed`
- `evidence.created`
- `learner_skill.updated`

### Session
- `session.started`
- `session.resumed`
- `session.ended`

## Terminal event example
```json
{
  "event_type": "terminal.command",
  "payload": {
    "command": "dig api.internal",
    "cwd": "/workspace",
    "terminal_id": "term_1",
    "sequence": 18
  }
}
```

Command output may be stored separately/chunked if large but must remain referenceable.

## Highlight model

A highlight must survive reload and content updates when possible.

```json
{
  "highlight_id": "hl_123",
  "content_id": "concept.network.dns",
  "content_version": "0.1.0",
  "selected_text": "recursive resolver",
  "start_offset": 148,
  "end_offset": 166,
  "semantic_anchor": "mechanism.steps[2].label",
  "context_before": "The OS forwards the query to a ",
  "context_after": " which may query authoritative servers.",
  "created_at": "..."
}
```

Prefer semantic anchors over raw DOM paths.

## AI quote/reference model
An AI user message can contain:
```json
{
  "text": "Why is this necessary?",
  "references": [
    {
      "type": "highlight",
      "id": "hl_123"
    }
  ]
}
```

The rendered UI should visibly quote the selected source.

## Learner-model update event
```json
{
  "event_type": "learner_skill.updated",
  "payload": {
    "skill_id": "network.dns.resolution",
    "previous": {"mastery_probability": 0.54},
    "next": {"mastery_probability": 0.68},
    "evidence_ids": ["ev_88", "ev_89"],
    "model_version": "bkt-lite-v1"
  }
}
```

## Storage rule
Never mutate historical learning events.
Corrections are new events.

PII/secrets from terminal output require redaction hooks before persistence.
