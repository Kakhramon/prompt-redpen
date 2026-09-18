#!/usr/bin/env python3
"""Self-check for redpen. Run it directly: python3 test_redpen.py

No framework on purpose. It covers the parts that decide whether a prompt gets
blocked: the prefilter, model and effort detection, the secret scanner, and the
JSON the judge replies with.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("CLAUDE_PLUGIN_DATA", tempfile.mkdtemp(prefix="redpen-test-"))

import redpen  # noqa: E402
import secret_scan  # noqa: E402


def test_prefilter():
    w = redpen.worth_reviewing
    assert w("fix it", "claude-opus-5", "high"), "bare 'fix it' must be flagged"
    assert w("improve", "claude-sonnet-5", "medium")
    assert w("it's broken", "claude-sonnet-5", "medium")
    assert not w(
        "Fix the /health endpoint in server.py, which returns 500 after the "
        "redis upgrade. Find the cause before changing anything.",
        "claude-sonnet-5", "high"), "a concrete prompt must pass"
    # an anchor plus enough words is enough, even when it opens with a verb
    assert not w("update the version string in pyproject.toml to 0.2.0",
                 "claude-sonnet-5", "medium")


def test_transcript_for_cwd():
    """The CLI subcommands rebuild the project directory from the cwd."""
    got = redpen.transcript_for_cwd("/Users/someone/Projects/my-app")
    assert got.parent.name == "-Users-someone-Projects-my-app", got
    assert got.parent.parent == Path.home() / ".claude" / "projects"
    assert redpen.transcript_for_cwd("/tmp/a.b").parent.name.endswith("a-b"), "dots become dashes"


def test_model_fit():
    """Concrete prompts, so the vagueness checks stay out of the way."""
    w = redpen.worth_reviewing
    heavy = "Refactor the auth layer in `src/auth.py` and every caller of it."
    trivial = "Fix the typo in the heading of `docs/install.md`, it says Instal."
    assert "haiku" in w(heavy, "claude-haiku-4-5-20251001", "medium")
    assert not w(heavy, "claude-opus-5", "high"), "heavy work on opus is fine"
    assert "fable" in w(trivial, "claude-fable-5-1", "high")
    assert "effort" in w(trivial, "claude-haiku-4-5-20251001", "xhigh")
    assert not w(trivial, "claude-haiku-4-5-20251001", "low"), "cheap and small is fine"


def test_tiers():
    assert redpen.tier_of(redpen.normalise_model("claude-fable-5-1[1m]")) == ("fable", 4)
    assert redpen.tier_of("claude-opus-5") == ("opus", 3)
    assert redpen.tier_of("claude-sonnet-5") == ("sonnet", 2)
    assert redpen.tier_of("claude-haiku-4-5-20251001") == ("haiku", 1)
    assert redpen.tier_of("") == (None, 0)
    assert redpen.normalise_model("Claude-Opus-5[1m]") == "claude-opus-5"


def test_model_from_transcript():
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "transcript.jsonl"
        t.write_text(
            json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"model": "claude-opus-5[1m]"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"model": "<synthetic>"}}) + "\n")
        assert redpen.current_model(str(t), d) == "claude-opus-5", \
            "last real model wins, <synthetic> is skipped"

        # First prompt of a session: the named transcript does not exist yet, so
        # the newest sibling in the same directory answers instead.
        fresh = Path(d) / "brand-new.jsonl"
        assert redpen.current_model(str(fresh), d) == "claude-opus-5", "falls back to a sibling"

    os.environ.pop("ANTHROPIC_MODEL", None)
    with tempfile.TemporaryDirectory() as empty:
        assert redpen.current_model(str(Path(empty) / "x.jsonl"), empty) == "", \
            "nothing to read is not fatal"
        assert redpen.current_model(None, empty) == ""


def test_continuations_pass_mid_conversation():
    """A short reply mid-conversation is the commonest prompt there is."""
    w = redpen.worth_reviewing
    for word in ["ok", "continue", "go on", "yes", "next", "do it", "keep going",
                 "proceed", "more", "again", "no", "nope", "stop", "finish",
                 "thanks", "lgtm", "sure", "y", "n", "Continue.", "OK!"]:
        assert not w(word, "claude-opus-5", "high", has_history=True), word
        assert w(word, "claude-opus-5", "high", has_history=False), \
            f"{word!r} on the first prompt of a session is still too vague"


def test_continuations_do_not_swallow_real_prompts():
    """Only a bare acknowledgement passes; a real instruction is still reviewed."""
    w = redpen.worth_reviewing
    assert w("continue the refactor", "claude-opus-5", "high", has_history=True), \
        "this one carries an instruction, so it is not a bare continuation"
    assert w("fix it", "claude-opus-5", "high", has_history=True)
    assert w("ok now fix the thing", "claude-opus-5", "high", has_history=True)


def test_session_history():
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "t.jsonl"
        assert not redpen.session_has_history(str(t)), "no file means no history"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user"}}) + "\n")
        assert not redpen.session_has_history(str(t)), "the user alone is not history"
        with open(t, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "m"}}) + "\n")
        assert redpen.session_has_history(str(t)), "Claude has spoken"
    assert not redpen.session_has_history(None)


def test_usable_rewrite():
    """A rewrite with a hole in it must never be sent on the user's behalf."""
    good = "Fix the /health endpoint in server.py, which returns 500 since the redis upgrade."
    assert redpen.usable_rewrite(good, "fix it")
    for bad in [
        "Fix the bug in [file/location]. The problem is [describe the issue].",
        "Rename <the variable> in the file you meant.",
        "Update {{module}} to the new API.",
        "",
        "   ",
    ]:
        assert not redpen.usable_rewrite(bad, "fix it"), bad
    assert not redpen.usable_rewrite("fix it", "fix it"), "identical is not a rewrite"
    # Brackets that are part of real code must survive.
    assert redpen.usable_rewrite("Change items[0] to items[-1] in cart.py", "fix it")


def test_auto_mode_sends_the_rewrite():
    import io, contextlib
    good = "Fix the /health endpoint in server.py, which returns 500 since the redis upgrade."
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        redpen.send_with_rewrite("fix it", good, "refine", ["no file named"], "", "auto")
    out = json.loads(buf.getvalue())
    assert "decision" not in out, "auto mode must never block"
    assert good in out["hookSpecificOutput"]["additionalContext"]

    # A rewrite it could not finish is not sent; the user is told instead.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        redpen.send_with_rewrite("fix it", "Fix the bug in [where?]", "refine",
                                 ["no file named"], "", "auto")
    out = json.loads(buf.getvalue())
    assert "decision" not in out
    assert "hookSpecificOutput" not in out, "nothing unsafe may be attached"
    assert "Sent as written" in out["systemMessage"]


def test_auto_widens_only_on_the_fast_path():
    """Reviewing every prompt is only affordable when the judge answers fast."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    assert redpen.reviews_everything("ultra"), "ultra always reviews everything"
    assert not redpen.reviews_everything("auto"), "CLI judge is too slow for that"
    assert not redpen.reviews_everything("full")
    assert not redpen.reviews_everything("lite")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    try:
        assert redpen.reviews_everything("auto"), "fast path affords the wide net"
        assert not redpen.reviews_everything("full"), "full still uses the prefilter"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_modes_include_auto():
    assert redpen.MODES == ("off", "lite", "auto", "full", "ultra")
    for m in redpen.MODES:
        assert m in redpen.MODE_HELP, m


def test_effort_from_settings():
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / ".claude"
        cfg.mkdir()
        (cfg / "settings.json").write_text(json.dumps({
            "effortLevel": "max",
            "modelSettings": {"claude-opus-5": {"effortLevel": "medium"}},
        }))
        os.environ.pop("CLAUDE_EFFORT", None)
        assert redpen.current_effort("claude-opus-5", d) == "medium", "per-model wins"
        assert redpen.current_effort("claude-sonnet-5", d) == "max", "falls back to global"
        (cfg / "settings.local.json").write_text(json.dumps({"effortLevel": "low"}))
        assert redpen.current_effort("claude-sonnet-5", d) == "low", "local overrides"


def test_secrets():
    findings, redacted = secret_scan.scan(
        "here is the key sk-ant-api03-Xk92mNvQp1LrTyWz8BcDeFgHiJkLmNoPqRsTuV")
    assert len(findings) == 1, findings
    assert findings[0]["name"] == "anthropic-api-key"
    assert "sk-ant-api03" not in redacted
    assert "[REDACTED:anthropic-api-key]" in redacted

    assert secret_scan.scan("set API_KEY=YOUR_API_KEY in the env")[0] == [], "placeholder"
    assert secret_scan.scan("the commit is 3f2a1b9c4d5e6f708192a3b4c5d6e7f809a1b2c3")[0] == [], \
        "a 40-char git sha is not a credential"


def test_secret_never_logged():
    """Nothing written to disk may carry a live key."""
    scrubbed = redpen.scrub({"original": "token: sk-ant-api03-"
                                         "Xk92mNvQp1LrTyWz8BcDeFgHiJkLmNoPqRsTuV"})
    assert "sk-ant-api03" not in json.dumps(scrubbed), scrubbed


def test_extract_json():
    assert redpen.extract_json('```json\n{"verdict": "ok"}\n```')["verdict"] == "ok"
    assert redpen.extract_json('noise {"a": {"b": 1}} tail')["a"]["b"] == 1
    assert redpen.extract_json("not json at all") is None


def test_approval_words():
    assert redpen.APPROVE_RE.match("ok")
    assert redpen.APPROVE_RE.match("  Yes! ")
    assert not redpen.APPROVE_RE.match("ok but change the file first")
    assert redpen.ORIGINAL_RE.match("no")


def test_block_payload():
    """The block JSON must carry both spellings, so old and new builds agree."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        redpen.emit_block("because")
    out = json.loads(buf.getvalue())
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert out["hookSpecificOutput"]["blockReason"] == "because"
    assert out["decision"] == "block" and out["reason"] == "because"


def test_detect_host():
    claude = {"session_id": "s", "cwd": "/x", "prompt": "hi",
              "hook_event_name": "UserPromptSubmit", "prompt_id": "p"}
    codex = {"session_id": "s", "cwd": "/x", "prompt": "hi",
             "hook_event_name": "UserPromptSubmit", "model": "gpt-5",
             "turn_id": "t"}
    cursor = {"prompt": "hi", "attachments": []}
    assert redpen.detect_host(claude) == "claude"
    assert redpen.detect_host(codex) == "codex"
    assert redpen.detect_host(cursor) == "cursor"


def test_codex_translation():
    block = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                    "decision": "block", "blockReason": "no"},
             "decision": "block", "reason": "no"}
    out = redpen.for_codex(block)
    assert out == {"decision": "block", "reason": "no"}, out

    ctx = {"systemMessage": "redpen: rewrote it.",
           "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                  "additionalContext": "Refined: do the thing"}}
    out = redpen.for_codex(ctx)
    assert isinstance(out, str) and "Refined: do the thing" in out
    assert "redpen: rewrote it." in out

    assert redpen.for_codex({"systemMessage": "hi"}) == {"systemMessage": "hi"}


def test_cursor_translation():
    block = {"decision": "block", "reason": "too vague"}
    assert redpen.for_cursor(block) == {"continue": False,
                                        "user_message": "too vague"}
    # Cursor cannot show a message on a passing turn, so nothing goes out.
    assert redpen.for_cursor({"systemMessage": "hi"}) is None


def test_mode_survives_a_different_plugin_data_dir():
    """The bug: mode was written under $CLAUDE_PLUGIN_DATA, so a mode set from a
    skill landed somewhere the hook never read, and every prompt stayed blocked.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        old_cfg, old_env = redpen.CONFIG_FILE, os.environ.get("CLAUDE_PLUGIN_DATA")
        redpen.CONFIG_FILE = cfg
        try:
            os.environ["CLAUDE_PLUGIN_DATA"] = tmp + "/a"
            os.environ.pop("REDPEN_MODE", None)
            redpen.set_mode("off")
            # the hook runs with a different data dir than the command line did
            os.environ["CLAUDE_PLUGIN_DATA"] = tmp + "/b"
            mode, src = redpen.resolve_mode()
            assert mode == "off", (mode, src)
        finally:
            redpen.CONFIG_FILE = old_cfg
            if old_env is None:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = old_env


def test_a_decline_may_say_why():
    assert redpen.ORIGINAL_RE.match("no")
    assert redpen.ORIGINAL_RE.match("no i do not own")
    assert redpen.ORIGINAL_RE.match("nope, use mine")
    assert not redpen.ORIGINAL_RE.match("normalize the config loader")
    assert not redpen.ORIGINAL_RE.match("add a note to the README")


def test_block_text_is_not_reviewed_again():
    reason = redpen.build_message("clarify", ["too vague"], "do the thing", [], "")
    assert redpen.BLOCK_MARKER in reason


def test_the_judge_sees_the_previous_turn():
    """A short prompt is only vague without what it replies to. Send both."""
    seen = {}

    def fake(user_msg):
        seen["msg"] = user_msg
        return '{"verdict": "ok"}'

    real, redpen.judge_via_cli = redpen.judge_via_cli, fake
    key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        redpen.judge("the other file too", "claude-sonnet-5", "high", "/tmp",
                     "I renamed the flag in server.py; config.py uses it too.")
    finally:
        redpen.judge_via_cli = real
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    assert "<previous_turn>" in seen["msg"]
    assert "config.py uses it too" in seen["msg"]

    redpen.judge_via_cli = fake
    try:
        redpen.judge("fix it", "claude-sonnet-5", "high", "/tmp", "")
    finally:
        redpen.judge_via_cli = real
    assert "<previous_turn>" not in seen["msg"]


def test_answering_a_question():
    import tempfile

    def transcript(*turns):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for role, text in turns:
            f.write(json.dumps({"type": role,
                                "message": {"content": [{"type": "text",
                                                         "text": text}]}}) + "\n")
        f.close()
        return f.name

    asked = transcript(("user", "do the thing"),
                       ("assistant", "Do you own github.com/Kahero?"))
    assert redpen.answering_a_question(asked)

    told = transcript(("user", "do the thing"),
                      ("assistant", "Done. Pushed to main."))
    assert not redpen.answering_a_question(told)

    # The shape that started this: the question is mid-paragraph and the turn
    # ends on a statement. Requiring a trailing "?" missed it.
    midway = transcript(("user", "do the thing"),
                        ("assistant", "Still yours to answer: do you own "
                                      "github.com/Kahero from 2018? That decides "
                                      "transfer versus a new org name."))
    assert redpen.answering_a_question(midway)

    # a trailing tool call must not hide the question that came before it
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write(json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text",
                                                 "text": "Which one?"}]}}) + "\n")
    f.write(json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use",
                                                 "name": "Bash"}]}}) + "\n")
    f.close()
    assert redpen.answering_a_question(f.name)

    assert not redpen.answering_a_question("/nope/missing.jsonl")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
