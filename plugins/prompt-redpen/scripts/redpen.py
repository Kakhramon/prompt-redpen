#!/usr/bin/env python3
"""
redpen.py - the whole plugin in one script.

Hook mode (no args, JSON on stdin)
    UserPromptSubmit: review the prompt, block and ask for approval if needed.

CLI subcommands (used by the bundled skills)
    --mode                 print the active mode and where it came from
    --set-mode <mode>      off | lite | full | ultra
    --secrets              print how credentials are handled
    --set-secrets <mode>   block | redact | warn | off
    --scan "<text>"        list credentials found in text, and show it redacted
    --scan-file <path>     the same, for a file
    --allow-secret <id>    allowlist a false positive by its 16-char fingerprint
    --review "<prompt>"    review a prompt without blocking anything
    --report [n]           dump the last n redpen decisions, model and effort

Credentials are scanned before anything else happens, in every mode, so no key
reaches the judge model, the decision log or Claude.

Fails open on prompt quality, closed on credentials: if the scanner itself
errors the prompt is still allowed, but a match is never let through silently.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_scan  # noqa: E402

MODES = ("off", "lite", "full", "ultra")
DEFAULT_MODE = "full"

# Credential handling runs independently of the redpen mode, including when the
# redpen is off. A prompt carrying a live key must never reach the judge model,
# the decision log, or Claude.
SECRET_MODES = ("block", "redact", "warn", "off")
DEFAULT_SECRET_MODE = "redact"

CFG = {
    "judge_model_cli": "haiku",
    "judge_model_api": "claude-haiku-4-5-20251001",
    "judge_timeout": 28,  # the CLI path spends most of this on startup
    "state_ttl": 60 * 45,
    "tiers": {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4},
    "efforts": {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5},
    "transcript_tail_bytes": 200_000,
    "log_limit": 2000,
}

BYPASS_PREFIX = "raw:"
CONFIG_FILE = Path.home() / ".config" / "prompt-redpen" / "config.json"

APPROVE_RE = re.compile(
    r"^\s*(y|yes|yep|yeah|ok|okay|k|go|go ahead|proceed|approve[d]?|"
    r"do it|sure|send it|continue|1)\b[\s.!]*$", re.I)
ORIGINAL_RE = re.compile(
    r"^\s*(n|no|nope|as[- ]is|use mine|keep mine|original|send mine|2)\b[\s.!]*$", re.I)
HEAVY_RE = re.compile(
    r"\b(architect\w*|design\s+(a|the|our)|refactor\w*|migrat\w*|root[- ]cause|"
    r"debug|race condition|concurren\w*|distributed|security (review|audit)|"
    r"threat model|performance (issue|regression)|memory leak|schema|"
    r"algorithm|whole (repo|codebase)|across the codebase|rewrite|port \w+ to)\b", re.I)
TRIVIAL_RE = re.compile(
    r"\b(typo|rename|reformat|format this|add a comment|bump (the )?version|"
    r"what does .{0,40} (mean|do)|one[- ]liner|gitignore|changelog entry)\b", re.I)
VAGUE_RE = re.compile(
    r"^\s*(fix|improve|optimi[sz]e|clean up|make (it|this) better|refactor|"
    r"update|change|help|continue|do it|finish|it'?s broken|doesn'?t work|"
    r"not working|any ideas)\b", re.I)
ANCHOR_RE = re.compile(r"(`|```|https?://|@[\w./-]+|[\w/-]+\.[A-Za-z]{1,5}\b|\b\w+\(\))")

JUDGE_SYSTEM = """You review prompts a developer is about to send to a coding agent.
Reply with ONE JSON object and nothing else. No prose, no code fences.

Schema:
{
  "verdict": "ok" | "refine" | "clarify",
  "issues": ["short phrase", ...],
  "refined_prompt": "the prompt rewritten so the agent can act on it",
  "questions": ["question the user must answer", ...],
  "recommended_model": "haiku" | "sonnet" | "opus" | "fable" | "any",
  "recommended_effort": "low" | "medium" | "high" | "xhigh" | "any",
  "model_reason": "one short sentence, empty if model and effort are fine"
}

What a good prompt has (Anthropic's published prompting guidance):
- Clear and direct. If a colleague with no context would be confused, so is the
  agent.
- The why, not only the what. What the work is for shapes the answer.
- An action verb. "Change this function to X" beats "can you suggest changes".
- Explicit scope, including what to leave out. Models over-deliver otherwise.
- Output format stated when it matters.
- Positive framing. Say what to do, not what to avoid.
- No emphatic language. CAPS and "you MUST" cause over-triggering.
- Everything upfront, not dripped over several turns.
- General over prescriptive. "Think it through" beats a hand-written procedure.

Verdicts:
- "ok": actionable as written. Leave refined_prompt empty.
- "refine": intent is clear but the wording is loose. Rewrite it faithfully.
  Never invent requirements, file names, libraries or constraints the user did
  not imply. Keep their scope; do not expand the task.
- "clarify": you cannot tell what they want. At most three questions, and still
  give your best refined_prompt.

Model fit, cheapest tier that does the job:
- haiku: mechanical edits, renames, lookups, formatting.
- sonnet: ordinary feature and bugfix work in known files.
- opus: multi-file features, larger refactors, subtle debugging.
- fable: architecture, security review, cross-repo reasoning, the genuinely hard
  ones.
- "any" when the tier honestly does not matter.

Effort fit:
- low: short, scoped, mechanical work.
- medium: ordinary work where cost matters.
- high: normal coding default.
- xhigh: the hardest reasoning.
- Never recommend "max"; it overthinks with diminishing returns.
- "any" when effort does not matter.

Be conservative. Terse but unambiguous is "ok". Only flag model or effort when
the mismatch is obvious and costs real tokens."""


# ----------------------------------------------------------------- paths ----

def state_dir():
    d = os.environ.get("CLAUDE_PLUGIN_DATA") or str(Path.home() / ".claude" / "prompt-redpen")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_id(session_id):
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "nosession")


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def scrub(value):
    """Defence in depth: nothing leaves this process with a live key in it."""
    try:
        if isinstance(value, str):
            return secret_scan.redact(value, state_dir(), load_config())
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
    except Exception:
        return "[unscannable]"
    return value


def log(msg):
    if os.environ.get("PROMPT_REDPEN_DEBUG"):
        try:
            with open(state_dir() / "debug.log", "a") as f:
                f.write(f"{time.strftime('%F %T')} {scrub(str(msg))}\n")
        except Exception:
            pass


def record(entry):
    try:
        path = state_dir() / "decisions.jsonl"
        entry = scrub(entry)
        entry["ts"] = time.strftime("%F %T")
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        lines = path.read_text().splitlines()
        if len(lines) > CFG["log_limit"]:
            path.write_text("\n".join(lines[-CFG["log_limit"]:]) + "\n")
    except Exception as e:
        log(f"record failed: {e}")


# ------------------------------------------------------------------ mode ----

def resolve_mode():
    """Returns (mode, source). First hit wins."""
    try:
        m = (state_dir() / "mode").read_text().strip().lower()
        if m in MODES:
            return m, "set by /prompt-redpen:redpen-mode"
    except Exception:
        pass
    m = (os.environ.get("PROMPT_REDPEN_MODE") or "").strip().lower()
    if m in MODES:
        return m, "$PROMPT_REDPEN_MODE"
    try:
        m = str(json.loads(CONFIG_FILE.read_text()).get("defaultMode", "")).lower()
        if m in MODES:
            return m, str(CONFIG_FILE)
    except Exception:
        pass
    return DEFAULT_MODE, "built-in default"


def set_mode(mode):
    mode = (mode or "").strip().lower().split()[0] if mode.strip() else ""
    if mode not in MODES:
        print(f"Unknown mode {mode!r}. Valid modes: {', '.join(MODES)}")
        return 1
    (state_dir() / "mode").write_text(mode)
    print(f"prompt-redpen mode is now: {mode}")
    print(MODE_HELP[mode])
    return 0


MODE_HELP = {
    "off": "Redpen does nothing. /prompt-redpen:validate-prompt still works on demand.",
    "lite": "Heuristics only, no model call, never blocks. Warns when a prompt looks thin.",
    "full": "Reviews prompts that look thin or mismatched, and blocks for your approval.",
    "ultra": "Reviews every prompt and blocks unless it is clearly actionable.",
}


# --------------------------------------------------------------- secrets ----

SECRET_HELP = {
    "block": "Refuse the prompt outright. Nothing is resent for you; retype without the key.",
    "redact": "Replace each credential with a placeholder and ask you to confirm the clean version.",
    "warn": "Tell you what was found and send the prompt anyway. Not recommended.",
    "off": "No credential scanning at all.",
}


def resolve_secrets_mode():
    try:
        m = (state_dir() / "secrets-mode").read_text().strip().lower()
        if m in SECRET_MODES:
            return m, "set by /prompt-redpen:redpen-mode secrets"
    except Exception:
        pass
    m = (os.environ.get("PROMPT_REDPEN_SECRETS") or "").strip().lower()
    if m in SECRET_MODES:
        return m, "$PROMPT_REDPEN_SECRETS"
    m = str(load_config().get("secrets", "")).lower()
    if m in SECRET_MODES:
        return m, str(CONFIG_FILE)
    return DEFAULT_SECRET_MODE, "built-in default"


def set_secrets_mode(mode):
    mode = (mode or "").strip().lower().split()[0] if (mode or "").strip() else ""
    if mode not in SECRET_MODES:
        print(f"Unknown secrets mode {mode!r}. Valid: {', '.join(SECRET_MODES)}")
        return 1
    (state_dir() / "secrets-mode").write_text(mode)
    print(f"credential handling is now: {mode}")
    print(SECRET_HELP[mode])
    if mode in ("warn", "off"):
        print("Warning: prompts carrying live credentials will now reach the model.")
    return 0


def allow_secret(fingerprint_id):
    """Allowlist by fingerprint, so the file never stores the value itself."""
    fp = (fingerprint_id or "").strip()
    if not re.fullmatch(r"[0-9a-f]{16}", fp):
        print("Pass the 16-character id shown next to the finding, not the value itself.")
        return 1
    path = state_dir() / "allowed-secrets.txt"
    existing = path.read_text() if path.exists() else ""
    if fp in existing:
        print(f"{fp} is already allowed.")
        return 0
    with open(path, "a") as f:
        f.write(f"{fp}\n")
    print(f"Allowed {fp}. Matches with this fingerprint will pass from now on.")
    return 0


def cmd_scan(text):
    findings, redacted = secret_scan.scan(text, state_dir(), load_config())
    mode, source = resolve_secrets_mode()
    print(f"credential handling: {mode}  ({source})")
    if not findings:
        print("No credentials found.")
        return 0
    print(f"\n{len(findings)} finding(s):")
    print(secret_scan.describe(findings))
    print("\nredacted:\n" + redacted)
    print("\nIf one of these is a false positive, allow it by id:")
    print(f"  python3 {Path(__file__).name} --allow-secret <id>")
    return 0


# ------------------------------------------------- model and effort in use ----
# UserPromptSubmit is not told which model or effort level is active, and there
# is no environment variable for either. The transcript carries the model on
# every assistant line; effort lives in the settings files.

MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]+)"')


def settings_files(cwd):
    """Lowest precedence first."""
    return [Path.home() / ".claude" / "settings.json",
            Path(cwd or ".") / ".claude" / "settings.json",
            Path(cwd or ".") / ".claude" / "settings.local.json"]


def merged_settings(cwd):
    out = {}
    for f in settings_files(cwd):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(data, dict):
            out.update(data)
    return out


def normalise_model(name):
    """`claude-opus-5[1m]` and `opus` both reduce to a plain lowercase id."""
    return re.sub(r"\[[^\]]*\]", "", (name or "")).strip().lower()


def model_in_transcript(path):
    """Last real model named in a transcript's tail, or "" if there is none."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > CFG["transcript_tail_bytes"]:
                f.seek(size - CFG["transcript_tail_bytes"])
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"cannot read {path}: {e}")
        return ""
    for name in reversed(MODEL_RE.findall(tail)):
        # <synthetic> marks a message Claude Code generated itself.
        if not name.startswith("<"):
            return normalise_model(name)
    return ""


def transcript_for_cwd(cwd):
    """Where Claude Code keeps this directory's transcripts.

    The CLI subcommands are not given a transcript path the way the hook is, so
    they reconstruct the project directory from the working directory: Claude
    Code names it after the absolute path with every separator turned into a
    dash.
    """
    try:
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd or ".").resolve()))
        return Path.home() / ".claude" / "projects" / slug / "latest.jsonl"
    except Exception:
        return None


def current_model(transcript_path, cwd=None):
    """The model in use, from the transcript, the environment or the settings.

    On the first prompt of a session the named transcript does not exist yet,
    because nothing has been written to it. The newest other transcript in the
    same project directory is the previous session in this same project, which
    is the best available guess at the model still in use.
    """
    path = Path(transcript_path) if transcript_path else None
    if path and path.exists():
        found = model_in_transcript(path)
        if found:
            return found
    if path:
        try:
            siblings = sorted((f for f in path.parent.glob("*.jsonl") if f != path),
                              key=lambda f: f.stat().st_mtime, reverse=True)
        except Exception:
            siblings = []
        for sibling in siblings[:3]:
            found = model_in_transcript(sibling)
            if found:
                return found
    env = normalise_model(os.environ.get("ANTHROPIC_MODEL", ""))
    return env or normalise_model(merged_settings(cwd).get("model", ""))


def current_effort(model, cwd=None):
    """Per-model effort wins over the global one.

    # ponytail: settings files only. A mid-session /effort switch is invisible
    # to hooks; add a PostModelSwitch hook if that ever starts mattering.
    """
    env = (os.environ.get("CLAUDE_EFFORT") or "").strip().lower()
    if env in CFG["efforts"]:
        return env
    s = merged_settings(cwd)
    per_model = s.get("modelSettings") or {}
    if isinstance(per_model, dict):
        for key, val in per_model.items():
            if normalise_model(key) == model and isinstance(val, dict):
                lvl = str(val.get("effortLevel", "")).lower()
                if lvl in CFG["efforts"]:
                    return lvl
    lvl = str(s.get("effortLevel", "")).lower()
    return lvl if lvl in CFG["efforts"] else ""


def tier_of(model_str):
    for name, rank in CFG["tiers"].items():
        if name in (model_str or ""):
            return name, rank
    return None, 0


# ------------------------------------------------- pending approval state ----

def pending_path(session_id):
    return state_dir() / f"{safe_id(session_id)}.pending.json"


def read_pending(session_id):
    p = pending_path(session_id)
    try:
        data = json.loads(p.read_text())
        if time.time() - data.get("ts", 0) > CFG["state_ttl"]:
            p.unlink(missing_ok=True)
            return None
        return data
    except Exception:
        return None


def write_pending(session_id, data):
    data["ts"] = time.time()
    tmp = pending_path(session_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(pending_path(session_id))


def clear_pending(session_id):
    pending_path(session_id).unlink(missing_ok=True)


# ------------------------------------------------------------- prefilter ----

def worth_reviewing(prompt, model_name, effort):
    words = len(prompt.split())
    anchored = bool(ANCHOR_RE.search(prompt))
    if words <= 4:
        return "very short"
    if VAGUE_RE.match(prompt) and not anchored:
        return "vague opener with nothing concrete"
    if words < 12 and not anchored:
        return "no file, code or identifier to work from"
    name, rank = tier_of(model_name)
    eff = CFG["efforts"].get(effort or "", 0)
    if HEAVY_RE.search(prompt) and rank and rank < 3:
        return f"heavy task on {name}"
    if TRIVIAL_RE.search(prompt) and rank >= 3:
        return f"trivial task on {name}"
    if TRIVIAL_RE.search(prompt) and eff >= 3:
        return f"trivial task at {effort} effort"
    return ""


# --------------------------------------------------------------- judging ----

def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    start, depth = text.find("{"), 0
    if start < 0:
        return None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def judge_via_api(user_msg):
    body = json.dumps({
        "model": CFG["judge_model_api"],
        "max_tokens": 900,
        "system": JUDGE_SYSTEM,
        "messages": [{"role": "user", "content": user_msg}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=CFG["judge_timeout"]) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", []))


def judge_via_cli(user_msg):
    exe = shutil.which("claude")
    if not exe:
        return None
    env = dict(os.environ, PROMPT_REDPEN_MODE="off")  # stop the hook recursing
    proc = subprocess.run(
        [exe, "-p", f"{JUDGE_SYSTEM}\n\n---\n\n{user_msg}",
         "--model", CFG["judge_model_cli"],
         "--settings", '{"disableAllHooks": true}'],
        capture_output=True, text=True, env=env, timeout=CFG["judge_timeout"])
    return proc.stdout


def judge_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or shutil.which("claude"))


def judge(prompt, model_name, effort, cwd):
    # Belt and braces: the hook already stops credential-bearing prompts, but
    # nothing reaches a judge model without passing through the scanner.
    prompt = secret_scan.redact(prompt, state_dir(), load_config())
    user_msg = (f"Working directory: {cwd}\n"
                f"Model in use: {model_name or 'unknown'}\n"
                f"Effort level: {effort or 'unknown'}\n\n"
                f"Prompt to review:\n<prompt>\n{prompt}\n</prompt>")
    try:
        raw = judge_via_api(user_msg) if os.environ.get("ANTHROPIC_API_KEY") \
            else judge_via_cli(user_msg)
    except Exception as e:
        log(f"judge failed: {e}")
        return None
    return extract_json(raw) if raw else None


# ------------------------------------------------------------- reporting ----

def model_note_for(rec_model, rec_effort, cur_model, cur_effort, reason):
    cur_name, cur_rank = tier_of(cur_model)
    bits = []
    if rec_model and rec_model != "any" and cur_rank and rec_model != cur_name:
        direction = "heavier" if CFG["tiers"][rec_model] > cur_rank else "lighter"
        bits.append(f"You are on {cur_name}; this task looks {direction} "
                    f"- consider /model {rec_model}.")
    cur_eff = CFG["efforts"].get(cur_effort or "", 0)
    rec_eff = CFG["efforts"].get(rec_effort or "", 0)
    if rec_eff and cur_eff and rec_eff != cur_eff:
        bits.append(f"Effort is {cur_effort}; {rec_effort} would suit it better.")
    if bits and reason:
        bits.append(f"({reason})")
    return " ".join(bits)


def build_message(verdict, issues, refined, questions, model_note):
    lines = []
    if verdict == "clarify":
        lines.append("This prompt is ambiguous. Before sending it:")
    elif verdict == "refine":
        lines.append("This prompt can be tightened up first:")
    else:
        lines.append("Prompt looks fine, but the model setup does not:")
    lines += [f"  - {i}" for i in issues[:4]]
    lines += [f"  ? {q}" for q in questions[:3]]
    if model_note:
        lines += ["", model_note]
    if refined:
        lines += ["", "Refined version:", "-" * 60, refined, "-" * 60]
    lines.append("")
    if refined:
        lines.append("Reply  ok  to send the refined version, "
                     "no  to send yours unchanged, or just retype it.")
    else:
        lines.append("Reply  ok  to send it anyway, or retype it.")
    return "\n".join(lines)


# --------------------------------------------------------- CLI subcommands ----

def cmd_review(text):
    text = (text or "").strip()
    if not text:
        print("Nothing to review. Usage: --review \"your prompt\"")
        return 0
    mode, source = resolve_mode()
    cwd = os.getcwd()
    model_name = current_model(transcript_for_cwd(cwd), cwd)
    effort = current_effort(model_name, cwd)
    verdict_data = judge(text, model_name, effort, cwd)
    if not verdict_data:
        if judge_available():
            print(f"The judge did not answer within {CFG['judge_timeout']}s. "
                  "Set ANTHROPIC_API_KEY to use the fast path, or try again.")
        else:
            print("No judge available: set ANTHROPIC_API_KEY, or put `claude` on PATH.")
        return 0
    print(f"verdict: {verdict_data.get('verdict', '?')}   (redpen mode: {mode})")
    for i in verdict_data.get("issues") or []:
        print(f"  - {i}")
    for q in verdict_data.get("questions") or []:
        print(f"  ? {q}")
    note = model_note_for((verdict_data.get("recommended_model") or "any").lower(),
                          (verdict_data.get("recommended_effort") or "any").lower(),
                          model_name, effort,
                          (verdict_data.get("model_reason") or "").strip())
    if note:
        print(f"\n{note}")
    refined = (verdict_data.get("refined_prompt") or "").strip()
    if refined:
        print("\nrefined:\n" + refined)
    record({"event": "review", "mode": mode, "original": text,
            "verdict": verdict_data.get("verdict"), "refined": refined})
    return 0


def cmd_report(limit):
    mode, source = resolve_mode()
    print(f"redpen mode: {mode}  ({source})")
    path = state_dir() / "decisions.jsonl"
    try:
        lines = path.read_text().splitlines()[-limit:]
    except Exception:
        lines = []
    if not lines:
        print("No redpen decisions recorded yet.")
    else:
        print(f"\nlast {len(lines)} decisions (newest last):")
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:
                continue
            print(json.dumps({k: v for k, v in e.items() if k != "refined"}))
    cwd = os.getcwd()
    model_name = current_model(transcript_for_cwd(cwd), cwd)
    effort = current_effort(model_name, cwd)
    print("\ncurrent setup:")
    print(f"  model: {model_name or 'unknown'}")
    print(f"  effort: {effort or 'unknown'}")
    return 0


# ------------------------------------------------------------- hook mode ----

def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def emit_block(reason):
    """Erase the prompt and show `reason` to the user.

    Both spellings go out together: `hookSpecificOutput.blockReason` is the
    documented one, the top-level pair is what older Claude Code builds read.
    An unknown key is ignored, so sending both is free.
    """
    emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                 "decision": "block",
                                 "blockReason": reason},
          "decision": "block",
          "reason": reason})


def handle_secrets(prompt, session):
    """Runs before anything else. Returns True if the turn is already decided."""
    sec_mode, _ = resolve_secrets_mode()
    if sec_mode == "off" or not prompt:
        return False
    findings, redacted = secret_scan.scan(prompt, state_dir(), load_config())
    if not findings:
        return False

    names = ", ".join(sorted({f["name"] for f in findings}))
    record({"event": f"secret_{sec_mode}", "findings": findings, "count": len(findings)})

    if sec_mode == "warn":
        emit({"systemMessage":
              f"prompt-redpen: {len(findings)} credential(s) in that prompt ({names}) "
              f"- sent anyway because credential handling is set to warn."})
        return False  # caller continues; the raw prompt goes through

    lines = [f"Stopped: that prompt contains {len(findings)} credential(s).",
             secret_scan.describe(findings),
             "",
             "The prompt was erased and never reached the model."]
    if sec_mode == "block":
        lines += ["", "Retype it without the credential. If this is a false positive, run:",
                  f"  python3 \"$CLAUDE_PLUGIN_ROOT/scripts/redpen.py\" "
                  f"--allow-secret {findings[0]['fingerprint']}"]
    else:  # redact
        write_pending(session, {"original": "", "refined": redacted, "secret": True})
        lines += ["", "Redacted version:", "-" * 60, redacted, "-" * 60, "",
                  "Reply  ok  to send the redacted version. There is no option to send "
                  "the original.",
                  f"False positive? --allow-secret {findings[0]['fingerprint']}"]
    emit_block("\n".join(lines))
    return True


def hook():
    data = json.load(sys.stdin)
    prompt = (data.get("prompt") or "").strip()
    session = data.get("session_id") or "nosession"
    cwd = data.get("cwd") or os.getcwd()
    model_name = current_model(data.get("transcript_path"), cwd)
    effort = current_effort(model_name, cwd)
    mode, _ = resolve_mode()
    log(f"stdin keys={sorted(data)} transcript={data.get('transcript_path')!r} "
        f"model={model_name!r} effort={effort!r} mode={mode}")

    # Credentials are checked first, in every redpen mode, and for slash commands
    # and raw: prompts too. Everything below this line may send text to a model.
    if handle_secrets(prompt, session):
        return 0

    # phase 2: answering a block from the previous turn. This runs before the
    # mode check because a credential block creates a pending approval even
    # when redpen itself is off, and that approval still has to resolve.
    pending = read_pending(session)
    if pending:
        if APPROVE_RE.match(prompt):
            clear_pending(session)
            record({"event": "approved", "mode": mode, "session": session})
            emit({"hookSpecificOutput": {
                      "hookEventName": "UserPromptSubmit",
                      "additionalContext":
                          "The user's short reply approves a refined request from the "
                          "prompt reviewer. The request for this turn is:\n\n"
                          + pending["refined"]},
                  "systemMessage": "Sending the refined prompt."})
            return 0
        if ORIGINAL_RE.match(prompt):
            if pending.get("secret"):
                emit_block("That prompt contained a credential, so the original "
                           "isn't kept anywhere. Reply  ok  to send the redacted "
                           "version, or retype the prompt.")
                return 0
            clear_pending(session)
            record({"event": "kept_original", "mode": mode, "session": session})
            emit({"hookSpecificOutput": {
                      "hookEventName": "UserPromptSubmit",
                      "additionalContext":
                          "The user chose to keep their original wording. The request "
                          "for this turn is:\n\n" + pending["original"]},
                  "systemMessage": "Sending your original prompt."})
            return 0
        clear_pending(session)

    if mode == "off":
        return 0
    if not prompt or prompt.startswith(("/", "#", "!")) \
            or prompt.lower().startswith(BYPASS_PREFIX):
        return 0

    # phase 1: review
    reason = worth_reviewing(prompt, model_name, effort)
    if mode != "ultra" and not reason:
        return 0

    if mode == "lite":
        record({"event": "warned", "mode": mode, "why": reason, "original": prompt})
        emit({"systemMessage":
              f"prompt-redpen: {reason or 'this prompt looks thin'}. "
              f"/prompt-redpen:validate-prompt for a rewrite."})
        return 0

    verdict_data = judge(prompt, model_name, effort, cwd)
    if not verdict_data:
        record({"event": "judge_unavailable", "mode": mode})
        return 0

    verdict = (verdict_data.get("verdict") or "ok").lower()
    refined = (verdict_data.get("refined_prompt") or "").strip()
    issues = verdict_data.get("issues") or []
    questions = verdict_data.get("questions") or []
    note = model_note_for((verdict_data.get("recommended_model") or "any").lower(),
                          (verdict_data.get("recommended_effort") or "any").lower(),
                          model_name, effort,
                          (verdict_data.get("model_reason") or "").strip())

    if verdict == "ok" and not note:
        record({"event": "passed", "mode": mode, "original": prompt})
        return 0
    if not refined:
        refined = prompt

    write_pending(session, {"original": prompt, "refined": refined})
    record({"event": "blocked", "mode": mode, "verdict": verdict, "model": model_name,
            "effort": effort, "issues": issues, "original": prompt, "refined": refined})
    emit_block(build_message(verdict, issues, refined, questions, note))
    return 0


# ------------------------------------------------------------------ main ----

def main(argv):
    if not argv:
        return hook()
    cmd = argv[0]
    if cmd == "--mode":
        mode, source = resolve_mode()
        sec, sec_source = resolve_secrets_mode()
        print(f"prompt-redpen mode: {mode}  ({source})")
        print(MODE_HELP[mode])
        print(f"\ncredential handling: {sec}  ({sec_source})")
        print(SECRET_HELP[sec])
        return 0
    if cmd == "--set-mode":
        return set_mode(" ".join(argv[1:]))
    if cmd == "--secrets":
        sec, source = resolve_secrets_mode()
        print(f"credential handling: {sec}  ({source})")
        print(SECRET_HELP[sec])
        return 0
    if cmd == "--set-secrets":
        return set_secrets_mode(" ".join(argv[1:]))
    if cmd == "--allow-secret":
        return allow_secret(" ".join(argv[1:]))
    if cmd == "--scan":
        return cmd_scan(" ".join(argv[1:]))
    if cmd == "--scan-file":
        try:
            return cmd_scan(Path(argv[1]).read_text(errors="replace"))
        except Exception as e:
            print(f"Could not read that file: {e}")
            return 1
    if cmd == "--review":
        return cmd_review(" ".join(argv[1:]))
    if cmd == "--report":
        try:
            n = int(argv[1])
        except Exception:
            n = 20
        return cmd_report(n)
    print(__doc__)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]) or 0)
    except SystemExit:
        raise
    except Exception as exc:
        log(f"fatal: {exc!r}")
        sys.exit(0)
