---
name: validate-prompt
description: Review a prompt on demand without sending it, and show a rewritten version plus whether the current model and effort level fit the work. Use when the user wants a draft prompt checked before they send it, or asks how to phrase something better for a coding agent.
arguments: "<the prompt text to review>"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

The user wants a prompt reviewed before they send it. Their argument:
`$ARGUMENTS`

Run the reviewer and report what it printed:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --review "$ARGUMENTS"
```

This works in every mode, including `off`, and it never blocks anything. The
prompt is not sent to Claude as a request; it is only reviewed.

Report the verdict, the issues, any questions and the refined version as the
script printed them. Do not rewrite the refined prompt yourself and do not add
your own critique on top. If the user then asks you to act on the refined
version, treat that as a fresh request.

If the script says the judge is unavailable, say so in one line: it asks Haiku
through the `claude` CLI, so that has to be on `PATH`.
