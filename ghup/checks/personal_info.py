"""Personal info: username, PC name, real name, home folder, git name/email, home paths, emails, private IPs.

The values are read from this machine when the check runs, so nothing personal is written here.
"""
import re

from . import SAFE_EMAIL

NAME = "personal_info"
TITLE = "No personal info (names, paths, emails, IPs)"

PATTERNS = [
    ("home folder path", re.compile(r"(/home/|/Users/|[A-Z]:\\Users\\)(?!(you|user|username|me|example|runner)\b)[\w.-]+")),
    ("email address", re.compile(r"\b[\w.+-]+@(gmail|googlemail|yahoo|outlook|hotmail|live|proton|protonmail|pm|icloud|me|gmx|aol|yandex|mail)\.\w+", re.I)),
    # a single address on a home network (not a range like 10.0.0.0/8, which code may need)
    ("home-network IP", re.compile(r"\b(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b(?!/\d)")),
]


def run(ctx):
    found = []
    for where, kind, line in ctx.texts():
        if where == "commit author/committer":
            if not SAFE_EMAIL.search(line):
                found.append(ctx.finding(NAME, where, "commit email is not a GitHub no-reply address", line))
            continue
        hits = ctx.personal_hits(line, where)
        for label, rx in PATTERNS:
            for m in rx.finditer(line):
                if not (label == "email address" and SAFE_EMAIL.search(m.group(0))):
                    hits.append(label)
                    break
        if hits:
            found.append(ctx.finding(NAME, where, ", ".join(sorted(set(hits))), line))
    return found
