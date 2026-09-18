---
name: analyze-chat
description: Summarise what redpen has recorded, how many prompts were blocked, warned or passed, which issues keep recurring, and whether the model and effort level have matched the work. Use when the user asks how their prompting is going, what redpen has caught, or where they are wasting tokens.
arguments: "[how many decisions to read, default 50]"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

The user wants a review of their prompting habits. Their argument, which may be
empty: `$ARGUMENTS`

Read the decision log. Use the number they gave, or 50:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --report 50
```

Each line is one JSON decision. The `event` field is what happened: `passed`,
`warned`, `blocked`, `approved`, `kept_original`, `review`, `judge_unavailable`,
or `secret_block` / `secret_redact` / `secret_warn`. The `issues` field on a
blocked entry holds the judge's reasons, and `model` and `effort` record what
was active at the time.

Write a short summary covering:

- How many prompts were reviewed, and the split between passed, warned and
  blocked.
- The two or three issues that recur most, quoted from the `issues` fields.
- Whether blocked prompts were usually approved or retyped. Mostly retyped means
  the rewrites were not landing.
- Model and effort fit: prompts flagged as heavy on a light model, or trivial on
  a heavy one or at high effort. Name the pattern, not every instance.
- Two concrete suggestions for their next prompts, drawn from what the log
  actually shows.

Keep it under twenty lines. If the log is empty, say so and mention that
decisions only accumulate while the mode is `lite` or stronger.

The log is already redacted: credentials were replaced with placeholders before
anything was written. Keep it that way in your summary.
