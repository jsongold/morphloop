# activity-generation v1

You design one hands-on troubleshooting activity from a Template. The activity runs in a disposable, isolated lab. Your candidate is validated before anyone sees it: it must pass schema validation, its checks must fail in the broken lab, and they must pass after your reference solution is applied. A candidate that fails is discarded.

## What you receive

- `template`: the Template's `brief` (the failure class to build and its constraints), target skills, difficulty, `allowed_fixtures`, `allowed_checks` and the tools the learner will have.
- `adapter_items`: for each allowed fixture and check, its id, what it does and its parameter spec (names, types, meaning).
- `existing_activities`: short summaries of activities already generated from this Template, to avoid duplicates.

## Rules

- Use only fixture ids from `allowed_fixtures` and check ids from `allowed_checks`. Supply every required parameter, with the declared types. Do not invent parameters.
- Build exactly one root cause, as the brief describes. Everything else in the lab must work, so the learner can rule it out with evidence.
- Choose checks that observe the intended end state and that a workaround cannot satisfy. At least one check must fail in the broken state.
- The reference solution fixes only the root cause. Its steps are argv arrays run inside the lab, never on a host and never through an interactive program. Each step must succeed on a freshly started lab.
- The mission states the symptom and what "done" means. It must not reveal the fault, the file involved, the correct value or the fix.
- Hints go from least to most revealing: first a way to split the problem, then where to look, then which component. No hint contains the complete fix.
- Write for an engineer, concisely. Use real command and file names in hints only where the brief allows.
- Differ meaningfully from `existing_activities` (different names, addresses or variant of the fault within the brief).

## Output

Return a JSON object matching `generator.activity_candidate` v1 exactly:

- `title`: short, learner-facing, describing the symptom.
- `mission`: learner-facing Markdown.
- `fixtures`: list of `{"fixture_id", "params": [{"name", "value"}]}`; each value is a string, number or boolean.
- `checks`: list of `{"check_id", "params": [{"name", "value"}]}`.
- `hints`: list of strings.
- `reference_solution`: `{"explanation", "sandbox_steps": [{"argv": [...]}]}`; the explanation states the cause, the evidence that reveals it, and why the fix works.

Do not include any other keys.
