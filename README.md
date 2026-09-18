# redpen

A Claude Code plugin that reads your prompt before Claude does.

If the prompt is too vague to act on, it stops the turn, shows you a rewritten
version, and waits. Reply `ok` and the refined version goes through. If the task
doesn't match the model or effort level you're on, it says so before you spend
the tokens.

```
> fix it

This prompt is ambiguous. Before sending it:
  - no file or symbol named
  ? Which endpoint is failing?

You are on fable; this task looks lighter - consider /model sonnet.

Refined version:
------------------------------------------------------------
Fix the /health endpoint in server.py, which started returning 500
after the redis upgrade. Find the cause before changing anything.
------------------------------------------------------------

Reply  ok  to send the refined version, no  to send yours unchanged,
or just retype it.
```

## Install

```
/plugin marketplace add kakhramon/prompt-redpen
/plugin install redpen@prompt-redpen
```

Then start a new session. Run `/hooks` to see the one hook it registers, and
`/redpen:mode` to see what it's doing.

Requires Python 3.8+ on `PATH` as `python3`. On Windows, change `python3` to
`python` in `plugins/redpen/hooks/hooks.json`.

The default mode blocks and waits. If you would rather be warned than stopped,
run `/redpen:mode lite` once and it stays that way.

**The judge.** Reviews are done by Haiku. With `ANTHROPIC_API_KEY` set, redpen
calls the API directly, in about a second, billed to that key. Without it, it
shells out to `claude -p --model haiku`, which uses your normal auth but pays
for CLI startup every time: ten to twenty seconds in practice. Either way, if
the judge doesn't answer within 28 seconds the prompt goes through untouched.

## Modes

```
/redpen:mode          # what's active now
/redpen:mode lite     # change it
```

| mode | judge runs | on a thin prompt | model-fit check |
|---|---|---|---|
| `off` | never | nothing | no |
| `lite` | never | one-line warning, prompt still goes through | no |
| `full` | when the prefilter fires | blocks, waits for your `ok` | yes |
| `ultra` | every prompt | blocks unless clearly actionable | yes |

`full` is the default. `lite` costs nothing and is the one to fall back to if
the blocking gets annoying.

The mode is global on the machine and persists across sessions. Set the starting
mode with `REDPEN_MODE=lite` in your shell, or `{"defaultMode": "lite"}`
in `~/.config/redpen/config.json`. Precedence:
`/redpen:mode` > env var > config file > `full`.

## Commands

| | |
|---|---|
| `/redpen:mode [mode]` | Show or change the mode |
| `/redpen:validate-prompt <text>` | Review a prompt on demand, without sending it. Works in every mode, including `off` |
| `/redpen:analyze-chat` | Review the session: what you asked, which prompts needed rework, whether the model fit the work |
| `/redpen:check-secrets <text\|path>` | Scan text or a file for credentials and show it redacted |

## What a good prompt looks like

The judge scores against Anthropic's own published prompting guidance, not a
house style. In short:

- **Clear and direct.** If a colleague with no context would be confused, so is
  the model.
- **Say why.** What the work is for changes what a good answer looks like.
- **Use an action verb.** "Change this function to X" gets an edit; "can you
  suggest changes" gets a list.
- **State the scope, including what to leave out.** Models over-deliver when you
  don't.
- **Frame it positively** and skip the emphatic language. CAPS and "you MUST"
  cause over-triggering.
- **Put it all in one prompt.** Dripping requirements over several turns costs
  more tokens and often lands worse.

Sources: the [prompt engineering
overview](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview),
[best
practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices),
and the per-model pages for
[Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1),
[Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5),
[Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5),
[Opus 4.8](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-4-8)
and
[Sonnet 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5).

## Model and effort fit

The judge picks the cheapest tier that still does the job, and redpen compares
that to what you're actually on.

| tier | the work it's for |
|---|---|
| `haiku` | mechanical edits, renames, lookups, formatting |
| `sonnet` | ordinary feature and bugfix work in known files |
| `opus` | multi-file features, larger refactors, subtle debugging |
| `fable` | architecture, security review, cross-repo reasoning |

Effort runs `low` through `max`. The judge never recommends `max`, which
overthinks for diminishing returns. It flags the two cases that cost real money:
a heavy task on a light model, which burns a turn and gets redone, and a trivial
task on a heavy model or at high effort, which pays reasoning prices for a
rename.

It only ever prints a nudge. Redpen never switches your model for you.

## Credentials

Before anything else happens to a prompt - before the judge is called, before
anything is written to the log, before Claude sees it - redpen scans it for
credentials. This runs in **every** mode, including `off`, and applies to slash
commands and `raw:` prompts too.

```
> here's the key so you can test it: sk-ant-api03-Xk92mNvQp1LrTyWz8Bc...

Stopped: that prompt contains 1 credential(s).
  - anthropic-api-key on line 1: sk-a************************sTuV   (id 8648e9475bf83088)

The prompt was erased and never reached the model.

Redacted version:
------------------------------------------------------------
here's the key so you can test it: [REDACTED:anthropic-api-key]
------------------------------------------------------------

Reply  ok  to send the redacted version. There is no option to send the original.
```

| handling | what happens on a match |
|---|---|
| `block` | Prompt refused and erased. Nothing is resent; retype it |
| `redact` | Prompt erased, credential replaced with a placeholder, `ok` sends the clean version (default) |
| `warn` | You're told what was found, prompt goes through unchanged |
| `off` | No scanning |

```
/redpen:mode secrets            # show current handling
/redpen:mode secrets block      # change it
/redpen:check-secrets ./deploy.sh      # scan something on demand
```

Set the default with `REDPEN_SECRETS=block` or `{"secrets": "block"}` in
`~/.config/redpen/config.json`.

**What it catches:** AWS access keys, Anthropic / OpenAI / Google / Stripe /
SendGrid / Twilio / npm / PyPI / HuggingFace keys, GitHub and GitLab tokens,
Slack tokens and webhooks, Discord webhooks, JWTs, `Bearer` and `Basic` headers,
private key blocks, credentials embedded in database and other URLs, and
`token = "..."`-style assignments whose value is long and high-entropy.

**What it skips**, so it isn't crying wolf constantly: placeholders
(`YOUR_API_KEY`, `<token>`, `${GITHUB_TOKEN}`, `changeme`), and bare hex strings
of digest length, since a git SHA is 40 hex characters and would otherwise fire
on every other prompt.

**False positives** are allowlisted by fingerprint, never by value:

```
/redpen:check-secrets ...     # prints an id next to each finding
python3 .../redpen.py --allow-secret 8648e9475bf83088
```

The allowlist file holds only hashes, so it is safe to read, sync and commit.

In-house token formats go in `extraSecretPatterns`:

```json
{
  "secrets": "redact",
  "extraSecretPatterns": [
    { "name": "acme-service-token", "regex": "\\bacme_[a-z]{4}_[A-Za-z0-9]{32}\\b" }
  ]
}
```

**What this does not cover.** It reads your prompts, not your repository. A key
that Claude reads out of a file with the Read or Bash tool never passes through
this hook. If you want that too, the place to do it is a `PreToolUse` hook,
which - unlike `UserPromptSubmit` - can rewrite a tool's arguments in place via
`updatedInput`. And redpen is a safety net, not a secret manager: a key that
reached a prompt should be rotated, not just redacted.

## What gets reviewed

Most prompts are never sent to the judge, so they cost no extra latency. In
`full` mode a local prefilter escalates only when the prompt is very short, or
short with nothing concrete in it (no path, code, URL or identifier), or opens
with a bare `fix` / `improve` / `it's broken`, or looks heavy while you're on a
small model, or looks trivial while you're on a large one or at high effort.

A short reply that only continues the conversation is never reviewed. `ok`,
`continue`, `go on`, `yes`, `no`, `next`, `more`, `again`, `stop`, `thanks` and
the rest go straight through, because mid-conversation the transcript is the
context and reviewing them in isolation is both useless and infuriating. The
same words on the very first prompt of a session are still thin, and are still
treated that way, so the transcript decides rather than the wording.

Adding anything to one takes it out of that class: `continue` passes, `continue
the refactor` gets reviewed like any other prompt.

Tune the regexes at the top of `plugins/redpen/scripts/redpen.py`:
`VAGUE_RE`, `HEAVY_RE`, `TRIVIAL_RE`, `ANCHOR_RE`, `CONTINUE_RE`.

## Escape hatches

| | |
|---|---|
| Skip one prompt | start it with `raw:` |
| Turn it off for a shell session | `export REDPEN_MODE=off` |
| Turn it off everywhere | `/redpen:mode off` |
| Uninstall | `/plugin uninstall redpen@prompt-redpen` |
| See what it's doing | `export REDPEN_DEBUG=1`, then read `debug.log` in the plugin data dir |

`/`, `#` and `!` prefixed input always passes straight through.

## How it works

One hook and one script.

`UserPromptSubmit` is the gate. It can allow, block with a reason shown to you,
or attach context - but it **cannot rewrite your prompt text**, which is why
approval takes a second turn. On `ok`, the hook injects the refined text as
`additionalContext`; the blocked prompt was erased from Claude's context, so
that injection is what Claude actually works from.

`UserPromptSubmit` isn't told which model or effort level is active, and there's
no environment variable for either. Redpen reads the model from the tail of the
session transcript, whose assistant lines each carry one. On the first prompt of
a session that file doesn't exist yet, so it falls back to the newest other
transcript in the same project, which is the previous session in the same
directory. The effort level comes from your settings files, checking
`modelSettings.<model>.effortLevel` before the global `effortLevel`.

Every decision is appended to `decisions.jsonl` in the plugin's data directory
(`${CLAUDE_PLUGIN_DATA}`, which survives plugin updates). Blocked prompts never
reach the transcript, so that log is what `/redpen:analyze-chat` reads.

## Development

```
claude plugin validate ./plugins/redpen --strict
python3 plugins/redpen/scripts/test_redpen.py
claude --plugin-dir ./plugins/redpen
```

`--plugin-dir` loads it for one session straight from the working copy, so you
can iterate without installing. A GitHub Action runs the validator and the
self-check on every push.

One trap, if you fork this into a plugin of your own: `hooks/hooks.json` is
loaded automatically by its name alone. Listing it under `hooks` in the manifest
as well loads it twice, and the plugin refuses to start with "Duplicate hooks
file detected". `claude plugin validate --strict` passes either way. Only
installing it surfaces the problem, so install your own plugin once before you
tell anyone about it.

To ship an update, bump `version` in
`plugins/redpen/.claude-plugin/plugin.json` and push. Users get it on
`/plugin marketplace update prompt-redpen`.

## Known rough edges

- **Two turns per correction.** Unavoidable while `UserPromptSubmit` has no way
  to replace the prompt text.
- **Latency on the CLI judge path.** Ten to twenty seconds, all of it CLI
  startup. Set an API key, or run in `lite`.
- **The judge can be wrong about model fit.** It sees one prompt with no repo
  context. The `/model` line is a nudge; redpen never switches models for you.
- **A mid-session `/effort` or `/model` change is invisible** until it reaches a
  settings file or the transcript.
- **Approval words are matched literally** (`ok`, `yes`, `go`, `proceed`).
  Anything longer is treated as a fresh prompt, which is the safe default.
- **The scanner is pattern-based.** It will miss a credential format it doesn't
  know and occasionally flag a long random-looking string that isn't one. Treat
  it as a seatbelt, not a guarantee.
- **Mode is global, not per-session.** Changing it in one terminal changes it in
  all of them.

MIT licensed.
