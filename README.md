# Adaptive Interactive Learning OS / Harness — Claude Code Handoff

This package contains the implementation specification for an open-source domain-agnostic adaptive performance-learning engine.

Start with:

1. `CLAUDE.md`
2. `docs/PRODUCT.md`
3. `docs/MVP.md`
4. `docs/ACCEPTANCE_CRITERIA.md`
5. the remaining architecture documents.

## Core idea

```text
Assess
→ infer weakness
→ generate/select practical mission
→ learner operates real environment
→ capture actions
→ evaluate evidence
→ update learner model
→ choose next training
→ repeat
```

The first subject pack is Software Engineering.

The product is practice-first: terminal/editor/browser/database activities are primary. Text, diagrams and animations exist to explain or remediate, not to replace practice.

## Key UX rule

Every abstraction should have a path to the actual implementation and observable system state:

`abstract → mechanism → implementation → observation → operation`

## Initial development command for Claude Code

Use the prompt in `START_CLAUDE_CODE.md`.
