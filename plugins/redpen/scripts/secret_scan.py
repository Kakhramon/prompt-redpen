#!/usr/bin/env python3
"""
secret_scan.py - finds credentials in text and redacts them.

Used by prompt_gate.py before anything else happens to a prompt: before the
judge is called, before anything is written to the decision log, and before the
prompt reaches Claude.

Two layers:
  1. Named patterns for credential formats that are unambiguous on sight.
  2. An entropy fallback for assignments like `token = "..."` where the value
     looks random, with the usual false-positive sources excluded.

scan(text) -> (findings, redacted_text)
"""

import base64
import hashlib
import json
import math
import os
import re
from pathlib import Path

# Each entry: (name, compiled regex, group holding the secret).
# Group 0 means the whole match is the secret.
PATTERNS = [
    ("aws-access-key-id", re.compile(r"\b((?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16})\b"), 1),
    ("anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), 0),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), 0),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"), 0),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}"), 0),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), 0),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), 0),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/+]{20,}"), 0),
    ("discord-webhook", re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{20,}"), 0),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), 0),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), 0),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"), 0),
    ("twilio-sid", re.compile(r"\b(?:AC|SK)[0-9a-fA-F]{32}\b"), 0),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), 0),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_\-]{16,}"), 0),
    ("hugging-face-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}"), 0),
    ("private-key-block", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
        r"[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"), 0),
    ("ssh-private-key-header", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), 0),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{20,})"), 1),
    ("basic-auth-header", re.compile(r"(?i)\bbasic\s+([A-Za-z0-9+/]{16,}={0,2})"), 1),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:([^\s/@]{3,})@"), 1),
]

# Values that look like secrets but are assignments; the value is group 2.
ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:api[_\-]?key|secret[_\-]?key|secret|token|access[_\-]?token|"
    r"refresh[_\-]?token|auth[_\-]?token|client[_\-]?secret|private[_\-]?key|"
    r"password|passwd|pwd|passphrase|credential|session[_\-]?key)s?)"
    r"\s*[:=]\s*[\"']?([^\s\"',;]{8,})[\"']?")

# Obvious stand-ins that should never be treated as live credentials.
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:x{3,}|\*{3,}|\.{3,}|<[^>]*>|\{\{?[^}]*\}?\}|\$\{?[A-Z0-9_]+\}?|"
    r"your[_\-]?\w*|my[_\-]?\w*|some[_\-]?\w*|example\w*|sample\w*|dummy\w*|"
    r"placeholder\w*|changeme\w*|redacted\w*|removed|none|null|true|false|"
    r"test|testing|password|secret|token|abc123|foo|bar|baz|\d+)$")

# Hex strings of these lengths are usually digests, not credentials.
DIGEST_LENGTHS = {7, 8, 32, 40, 64, 128}
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
ENTROPY_FLOOR = 3.6
MIN_ENTROPY_LEN = 16


def shannon_entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_placeholder(value):
    v = value.strip().strip("\"'")
    if len(set(v.lower())) <= 2:
        return True
    return bool(PLACEHOLDER_RE.match(v))


def looks_like_digest(value):
    return HEX_RE.match(value) and len(value) in DIGEST_LENGTHS


def fingerprint(value):
    """Short hash, so an allowlist never has to store the secret itself."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def load_allowlist(state_dir):
    try:
        raw = (Path(state_dir) / "allowed-secrets.txt").read_text()
    except Exception:
        return set()
    return {line.strip() for line in raw.splitlines()
            if line.strip() and not line.startswith("#")}


def load_extra_patterns(config):
    out = []
    for item in (config or {}).get("extraSecretPatterns") or []:
        try:
            out.append((item.get("name") or "custom",
                        re.compile(item["regex"]), int(item.get("group", 0))))
        except Exception:
            continue
    return out


def mask(value):
    v = value.strip()
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}{'*' * min(len(v) - 8, 24)}{v[-4:]}"


def scan(text, state_dir=None, config=None):
    """Returns (findings, redacted_text).

    findings: list of dicts with name, masked, fingerprint, line.
    """
    if not text:
        return [], text

    allow = load_allowlist(state_dir) if state_dir else set()
    hits = []  # (start, end, name, value)

    for name, rx, group in PATTERNS + load_extra_patterns(config):
        for m in rx.finditer(text):
            value = m.group(group) if group else m.group(0)
            if not value or looks_like_placeholder(value):
                continue
            start = m.start(group) if group else m.start(0)
            hits.append((start, start + len(value), name, value))

    for m in ASSIGNMENT_RE.finditer(text):
        value = m.group(2)
        if looks_like_placeholder(value) or looks_like_digest(value):
            continue
        if len(value) < MIN_ENTROPY_LEN or shannon_entropy(value) < ENTROPY_FLOOR:
            continue
        hits.append((m.start(2), m.end(2), f"{m.group(1).lower()}-assignment", value))

    # Drop overlaps, keeping the longest match at each position.
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept, last_end = [], -1
    for h in hits:
        if h[0] >= last_end:
            kept.append(h)
            last_end = h[1]

    findings, redacted, cursor = [], [], 0
    for start, end, name, value in kept:
        fp = fingerprint(value)
        if fp in allow or value in allow:
            continue
        findings.append({
            "name": name,
            "masked": mask(value),
            "fingerprint": fp,
            "line": text.count("\n", 0, start) + 1,
        })
        redacted.append(text[cursor:start])
        redacted.append(f"[REDACTED:{name}]")
        cursor = end
    redacted.append(text[cursor:])

    return findings, ("".join(redacted) if findings else text)


def redact(text, state_dir=None, config=None):
    return scan(text, state_dir, config)[1]


def describe(findings):
    lines = []
    for f in findings:
        lines.append(f"  - {f['name']} on line {f['line']}: {f['masked']}"
                     f"   (id {f['fingerprint']})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    data = sys.stdin.read() if len(sys.argv) < 2 else " ".join(sys.argv[1:])
    found, clean = scan(data)
    print(json.dumps({"findings": found, "redacted": clean}, indent=2))
