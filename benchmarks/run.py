#!/usr/bin/env python3
"""Measure the two things redpen claims to save.

    python3 benchmarks/run.py --reps 2

Arm 1, the wasted first pass. A vague prompt is sent to an agent in a small
repo. The agent reads files and guesses. That turn is measured, and so is the
cost of redpen's judge catching the same prompt instead.

Arm 2, the oversized model. One mechanical task is run on a small model and on
a large one. Both finish it. The difference is what the wrong tier costs.

Nothing here is estimated. Every number is `total_cost_usd` and `usage` as the
CLI reported it, and the raw runs are written next to the summary.
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGE_MODEL = "haiku"

SAMPLE = {
    "cart.py": '''def total(items):
    t = 0
    for i in items:
        t += i["price"] * i["qty"]
    return round(t, 2)


def apply_discount(total, pct):
    return total - (total * pct / 100)


def checkout(items, pct=0):
    return apply_discount(total(items), pct)
''',
    "test_cart.py": '''from cart import checkout


def test_checkout():
    assert checkout([{"price": 10.0, "qty": 2}]) == 20.0
''',
}

VAGUE = ["fix it", "improve this", "make it faster", "clean up the code",
         "it's broken"]

TRIVIAL = ("Rename the variable `t` to `running_total` in cart.py. "
           "Change nothing else.")


def workspace():
    d = Path(tempfile.mkdtemp(prefix="redpen-bench-"))
    for name, body in SAMPLE.items():
        (d / name).write_text(body)
    return d


def run(prompt, model, cwd, effort=None):
    """One headless turn. Returns the CLI's own accounting."""
    env = dict(os.environ, REDPEN_MODE="off")
    cmd = [shutil.which("claude") or "claude", "-p", prompt,
           "--output-format", "json", "--model", model,
           "--settings", '{"disableAllHooks": true}']
    if effort:
        cmd += ["--effort", effort]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                              env=env, timeout=600)
        data = json.loads(proc.stdout)
    except Exception as e:
        return {"error": repr(e), "wall_s": round(time.time() - t0, 1)}
    u = data.get("usage") or {}
    return {
        "cost_usd": data.get("total_cost_usd"),
        "input": u.get("input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "turns": data.get("num_turns"),
        "wall_s": round(data.get("duration_ms", 0) / 1000, 1),
    }


def judge_cost(prompt, cwd):
    """What it costs redpen to catch that prompt, using its real judge prompt."""
    sys.path.insert(0, str(ROOT.parent / "plugins/redpen/scripts"))
    import redpen
    msg = (f"{redpen.JUDGE_SYSTEM}\n\n---\n\nWorking directory: {cwd}\n"
           f"Model in use: unknown\nEffort level: unknown\n\n"
           f"Prompt to review:\n<prompt>\n{prompt}\n</prompt>")
    return run(msg, JUDGE_MODEL, cwd)


def mean(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return statistics.mean(vals) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default="sonnet",
                    help="the model a developer is assumed to be working on")
    ap.add_argument("--big", default="opus", help="oversized tier for arm 2")
    ap.add_argument("--small", default="haiku", help="right-sized tier for arm 2")
    ap.add_argument("--from", dest="src",
                    help="rebuild the markdown from a saved results json")
    args = ap.parse_args()

    if args.src:
        runs = json.loads(Path(args.src).read_text())
        stamp = Path(args.src).stem.replace("results-", "")
        print(f"wrote {write_report(runs, args, stamp)}")
        return

    runs = {"arm1_guess": [], "arm1_catch": [], "arm2_big": [], "arm2_small": []}

    print(f"arm 1: vague prompts on {args.model}, {args.reps} rep(s) each")
    for prompt in VAGUE:
        for _ in range(args.reps):
            d = workspace()
            g = run(prompt, args.model, d) | {"prompt": prompt}
            c = judge_cost(prompt, d) | {"prompt": prompt}
            runs["arm1_guess"].append(g)
            runs["arm1_catch"].append(c)
            print(f"  {prompt!r:20} guess ${g.get('cost_usd')}  catch ${c.get('cost_usd')}")
            shutil.rmtree(d, ignore_errors=True)

    print(f"\narm 2: one mechanical edit, {args.big} vs {args.small}")
    for _ in range(args.reps):
        for tier, key in ((args.big, "arm2_big"), (args.small, "arm2_small")):
            d = workspace()
            r = run(TRIVIAL, tier, d) | {"model": tier}
            runs[key].append(r)
            print(f"  {tier:8} ${r.get('cost_usd')}  {r.get('wall_s')}s")
            shutil.rmtree(d, ignore_errors=True)

    stamp = time.strftime("%Y-%m-%d")
    (ROOT / f"results-{stamp}.json").write_text(json.dumps(runs, indent=2))
    out = write_report(runs, args, stamp)
    print(f"\nwrote {out}")


def write_report(runs, args, stamp):
    g, c = mean(runs["arm1_guess"], "cost_usd"), mean(runs["arm1_catch"], "cost_usd")
    b, s = mean(runs["arm2_big"], "cost_usd"), mean(runs["arm2_small"], "cost_usd")
    gw, cw = mean(runs["arm1_guess"], "wall_s"), mean(runs["arm1_catch"], "wall_s")
    n = len(runs["arm1_guess"])
    L = [
        f"# redpen benchmark, {stamp}",
        "",
        f"`python3 benchmarks/run.py --reps {args.reps}`. {len(VAGUE)} vague prompts, "
        f"{args.reps} rep(s) each, {n} runs per arm, on a small sample repo. Every "
        "figure is `total_cost_usd` as the CLI reported it. Raw runs are in the "
        "matching `.json`.",
        "",
        "## Arm 1: what a vague prompt costs",
        "",
        "| | mean cost | mean wall |",
        "|---|--:|--:|",
        f"| letting `{args.model}` guess at it | ${g:.4f} | {gw:.0f}s |",
        f"| redpen catching it on `{JUDGE_MODEL}` | ${c:.4f} | {cw:.0f}s |",
        "",
        f"Catching costs **{c / g * 100:.0f}%** of guessing, and takes "
        f"**{cw / gw * 100:.0f}%** as long.",
        "",
        "## Arm 2: what the wrong tier costs",
        "",
        "| model | mean cost |",
        "|---|--:|",
        f"| `{args.big}` | ${b:.4f} |",
        f"| `{args.small}` | ${s:.4f} |",
        "",
        f"Same finished edit, **{b / s:.1f}x** the price.",
        "",
        "## What this does not show",
        "",
        "Arm 1 measures the first pass only. A guess is not always waste, and a",
        "caught prompt still has to be re-sent and paid for. What is saved is the",
        "wasted exploration, not the task.",
        "",
        "Arm 2 assumes the small model finishes the task correctly. Read the diffs",
        "before believing the ratio.",
        "",
        f"The sample is small, {args.reps} repetition(s) over {len(VAGUE)} prompts on",
        "one tiny repo. Treat these as an order of magnitude, not a measurement of",
        "your codebase.",
        "",
    ]
    out = ROOT / f"results-{stamp}.md"
    out.write_text("\n".join(L))
    return out


if __name__ == "__main__":
    main()
