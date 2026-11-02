"""Private dev notes: journey folders, plan, timeline, checkpoint and handoff files, dev-log phrases,
links to your dev-log folder, and your own private words (both set in config.toml).

These belong in the private dev-log folder, never in a public repo.
"""
import os
import re

NAME = "private_notes"
TITLE = "No private dev notes or dev-log links"

PRIVATE_FILE = re.compile(r"(^|/)(journey/|PLAN\.md$|CLAUDE\.md$|TEST-CASES\.md$|"
                          r"[^/]*(timeline|checkpoint|dev-?log|handoff)[^/]*\.md$)", re.I)
NOTES = [
    ("dev-log note", re.compile(r"(?i)\b(the )?user (asked|said|clarified|stated|wanted|has not yet)\b|"
                                r"\buser'?s answers\b|\bby the user, \d{4}-")),
    ("dev-log link", re.compile(r"(?i)(^|[\s(/])journey/\d\d-|\bPLAN\.md\b")),
    ("AI note", re.compile(r"(?i)\bAI continuation\b|\bhandoff note\b")),
]


def config_notes(cfg):
    """Patterns from the user's own config: the dev-log folder (full and ~ form) and private words."""
    notes = []
    if cfg and cfg.devlog_dir:
        home = os.path.expanduser("~")
        forms = {cfg.devlog_dir}
        if cfg.devlog_dir.startswith(home + os.sep):
            forms.add("~" + cfg.devlog_dir[len(home):])
        notes.append(("dev-log folder", re.compile("|".join(re.escape(f) for f in forms))))
    if cfg and cfg.private_words:
        notes.append(("private word", re.compile("|".join(re.escape(w) for w in cfg.private_words), re.I)))
    return notes


def run(ctx):
    found = []
    notes = NOTES + config_notes(ctx.config)
    for where, kind, line in ctx.texts():
        if kind == "name":
            if PRIVATE_FILE.search(line):
                found.append(ctx.finding(NAME, where, "private dev note: move it to your dev-log folder", line))
            continue
        hits = [label for label, rx in notes if rx.search(line)]
        if hits:
            found.append(ctx.finding(NAME, where, ", ".join(hits), line))
    return found
