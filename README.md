<h1 align="center">redpen</h1>

<p align="center">
  <em>Hands it back before you've spent anything on it.</em>
</p>

<p align="center">
  <a href="https://github.com/kakhramon/prompt-redpen/stargazers"><img src="https://img.shields.io/github/stars/kakhramon/prompt-redpen?style=flat-square&color=b3261e&label=stars" alt="Stars"></a>
  <a href="https://github.com/kakhramon/prompt-redpen/releases"><img src="https://img.shields.io/github/v/release/kakhramon/prompt-redpen?style=flat-square&color=b3261e&label=release" alt="Release"></a>
  <a href="https://github.com/kakhramon/prompt-redpen/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/kakhramon/prompt-redpen/validate.yml?style=flat-square&color=b3261e&label=checks" alt="Checks"></a>
  <img src="https://img.shields.io/github/last-commit/kakhramon/prompt-redpen?style=flat-square&color=b3261e&label=updated" alt="Last commit">
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-b3261e?style=flat-square" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/license-MIT-b3261e?style=flat-square" alt="MIT license">
</p>

---

You remember the teacher. The one who went through your spelling with a red pen
and handed it back looking like a crime scene. You hated it. You also stopped
spelling it *recieve*.

Then you left school and mostly stopped writing. For years the longest thing you
wrote was a commit message.

Now look at you. You write prompts all day. You have typed more words this month
than you handed in during five years of school, and not one of them gets marked.
The marking was the part that worked.

So: a red pen, for prompts.

Not to grade your English. To stop the turn that was about to be wasted, which in
this era costs money rather than a Saturday: the ask too vague to act on, the
rename you are paying a reasoning model to do, the API key you just pasted into
the chat.

## Before / after

You type `fix it`. Your agent reads six files, picks the bug it thinks you meant,
changes that one, and asks you to confirm. Two minutes and a few thousand tokens
later you find out it guessed wrong.

With redpen:

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

Nothing was sent. The turn has not started yet.

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

## Using it

There is nothing to run. Type as you always did; redpen only speaks up when it
has something to say, which on most prompts is never.

**When it stops you**, you have three replies:

| you type | what happens |
|---|---|
| `ok` | the refined version is sent instead of yours |
| `no` | your original is sent, unchanged |
| anything else | treated as a fresh prompt, so just retype it properly |

The one exception is a prompt that contained a credential. There `no` is refused,
because the original was erased rather than kept, and `ok` sends the redacted
version.

**When it only nudges you**, nothing is blocked. A line like `You are on fable;
this task looks lighter - consider /model sonnet` is advice. Switch with
`/model` if you agree, or carry on.

**When you want a second opinion before sending**, ask for one:

```
/redpen:validate-prompt rewrite the billing module to use the new tax API
```

That reviews the text and shows the rewrite without sending anything, and it
works even with redpen turned off.

**When it is in your way**, turn it down rather than off:

```
/redpen:mode auto     # rewrite and send, never block
/redpen:mode lite     # warn, never block, no model call
/redpen:mode off      # silence
raw: fix it           # skip redpen for this one prompt
```

`ok`, `continue`, `yes`, `next` and other bare replies are never reviewed
mid-conversation, so following up costs you nothing.

**When you want to know how you are doing**, read the log back:

```
/redpen:analyze-chat
```

It reports which prompts needed rework, which issues keep recurring, and whether
the model matched the work.

**The judge.** Reviews are done by Haiku. With `ANTHROPIC_API_KEY` set, redpen
calls the API directly, in about a second, billed to that key. Without it, it
shells out to `claude -p --model haiku`, which uses your normal auth but pays
for CLI startup every time: ten to twenty seconds in practice. Either way, if
the judge doesn't answer within 28 seconds the prompt goes through untouched.

redpen always fails open. A timeout, a missing key or no network means your
prompt is sent unreviewed rather than held, because a review tool that can lock
you out of your editor is worse than no review tool. It says so once per session
when that happens, so the quiet is never mistaken for approval.

## Modes

```
/redpen:mode          # what's active now
/redpen:mode lite     # change it
```

| mode | judge runs | what happens | blocks? |
|---|---|---|---|
| `off` | never | nothing | no |
| `lite` | never | one-line warning, prompt goes through as typed | no |
| `auto` | every prompt | a loose prompt is tightened and sent with the rewrite attached | no |
| `full` | when the prefilter fires | shows the rewrite and waits for your `ok` | yes |
| `ultra` | every prompt | as `full`, on everything | yes |

`full` is the default. `lite` costs nothing and is the one to fall back to if
the blocking gets annoying. `auto` is the one to pick if the correction is
welcome but the second turn is not.

**On `auto`.** It never stops you. When the judge finds a prompt whose intent
was clear and whose wording was loose, it attaches a tightened version and lets
the turn run. When it cannot rewrite one faithfully, it says what was missing
and sends your words unchanged.

Two things to know before turning it on. It reviews **every** prompt, because
the prefilter is tuned to catch prompts too vague to rewrite, which is the
opposite of what `auto` can use. So set `ANTHROPIC_API_KEY` first; without it
every prompt waits ten to twenty seconds for the command-line judge.

And a hook cannot replace your prompt text. The rewrite rides alongside what you
typed rather than instead of it. That steers a loose prompt, because a loose
prompt has nothing to contradict the rewrite, but it is an addition and not a
substitution.

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

- **Two turns per correction**, in `full` and `ultra`. Unavoidable while
  `UserPromptSubmit` has no way to replace the prompt text. `auto` trades the
  second turn for not being asked.
- **`auto` attaches, it does not substitute.** Your original words still reach
  the model. On a loose prompt that is invisible; on a prompt that already says
  something specific, your wording wins over the rewrite, which is the right
  outcome but not always the obvious one.
- **`auto` without an API key is slow**, because it reviews every prompt and the
  command-line judge costs ten to twenty seconds each time.
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
