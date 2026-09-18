---
name: check-secrets
description: Scan text or a file for API keys, tokens, passwords and private keys, and show it with the credentials redacted. Use when the user asks whether something contains secrets, wants a file checked before committing or pasting it, or asks about a credential redpen flagged.
arguments: "<text, or a path to a file>"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

The user wants something checked for credentials. Their argument: `$ARGUMENTS`

Never paste the contents of a suspect file into your own reply, and never read
the file with the Read tool first - that would pull the credential into the
transcript, which is the thing being avoided. Hand the path to the scanner
instead.

If the argument looks like a path:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --scan-file <path>
```

Otherwise:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --scan "$ARGUMENTS"
```

Report what the scanner printed. The findings are already masked; keep them that
way. If it found nothing, say so in one line and mention that the scanner knows
common credential formats plus high-entropy assignments, so an unusual in-house
token format can slip past - those go in `extraSecretPatterns` in
`~/.config/prompt-redpen/config.json`.

If the user says a finding is a false positive, allow it by its id, never by its
value:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/redpen.py" --allow-secret <16-char id>
```

If a real credential turns up in a file that is committed or shared, say the
obvious thing once: rotate it. Redacting the copy doesn't help if the key is
already in git history.
