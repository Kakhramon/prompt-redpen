---
name: mode
description: Show or change the redpen mode (off, lite, auto, full, ultra) or how it handles credentials (block, redact, warn, off). Use when the user asks about redpen's mode, wants it stricter or quieter, wants it turned off, or wants to change secret scanning.
arguments: "[off|lite|auto|full|ultra] | secrets [block|redact|warn|off]"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

The user is checking or changing the redpen mode. Their argument, which may
be empty: `$ARGUMENTS`

If the argument is empty, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --mode
```

If it names a mode (`off`, `lite`, `auto`, `full` or `ultra`), run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --set-mode <mode>
```

If it says something looser ("be stricter", "stop nagging me", "turn it off"),
pick the closest mode and say which one you picked:

| they want | mode |
|---|---|
| silence, no interruptions at all | `off` |
| warnings but never blocked, and no model call | `lite` |
| corrections applied for them, never blocked | `auto` |
| the normal behaviour back | `full` |
| every prompt reviewed, and blocked | `ultra` |

If they pick `auto`, mention once that it reviews every prompt, so it wants
`ANTHROPIC_API_KEY` set or each prompt waits on the command-line judge.

Then report the script's output in one or two lines. Don't add your own
explanation of the modes on top of what the script prints.

## Credentials

If the argument starts with `secrets`, they mean credential handling, which is a
separate setting from the redpen mode and applies even when redpen is off.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --secrets
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --set-secrets <mode>
```

Modes are `block` (refuse the prompt), `redact` (offer a cleaned version),
`warn` (say something and send it anyway) and `off` (no scanning).

If they ask for `warn` or `off`, set it, but tell them plainly what they've just
turned off: prompts carrying live keys will reach the model and be logged by
whoever runs it. Don't editorialise beyond one sentence.

The mode is global, not per-session: it applies to every Claude Code session on
this machine until it's changed again.
