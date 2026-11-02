"""Secrets: tokens, private keys, passwords in code, and secret files like .env."""
import os
import re

NAME = "secrets"
TITLE = "No secrets (tokens, keys, .env, passwords)"

PATTERNS = [
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{40,})")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password in code", re.compile(r"(?i)\b(password|passwd|secret|api_key|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]")),
]
SECRET_FILE = re.compile(r"(^|/)(\.env(\.[\w-]+)?|id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]*\.(pem|key|p12|pfx|kdbx)|"
                         r"\.netrc|\.pgpass|credentials(\.json)?|\.git-credentials)$", re.I)
SAFE_FILE = re.compile(r"(^|/)\.env\.(example|sample|template)$", re.I)


def run(ctx):
    found = []
    for where, kind, line in ctx.texts():
        if kind == "name":
            if SECRET_FILE.search(line) and not SAFE_FILE.search(line):
                found.append(ctx.finding(NAME, where, "secret file: must not be uploaded", os.path.basename(line)))
            continue
        hits = [label for label, rx in PATTERNS if rx.search(line)]
        if hits:
            # never print the secret itself
            shown = line
            for _, rx in PATTERNS:
                shown = rx.sub("[hidden]", shown)
            found.append(ctx.finding(NAME, where, ", ".join(hits), shown))
    return found
