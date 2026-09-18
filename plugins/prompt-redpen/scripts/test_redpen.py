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
            + json.dumps({"type": "assistant", "message": {"model": "claude-opus-5[1m]"}}) + "\n")
        assert redpen.current_model(str(t), d) == "claude-opus-5", "last model wins"
        assert redpen.current_model("/nope/missing.jsonl", d) == "", "missing file is not fatal"


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
