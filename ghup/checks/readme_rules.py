"""README layout: title, bold summary, section order, warning box last, no links to local docs/, no amateur status words."""
import os
import re

NAME = "readme_rules"
TITLE = "README style"

ORDER = ["what is", "features", "how it works", "quick start", "settings", "known limits", "troubleshooting",
         "status", "license"]
ORDER_TEXT = "What is, Features, How it works, Quick start, Settings, Known limits, Troubleshooting, Status, License"
NEEDED = ["README.md", "LICENSE"]
# docs/ stays local (only docs/images/ is uploaded), so the README must not link into it
DOCS_LINK = re.compile(r"\]\(\.?/?docs/(?!images/)[^)]*\)")
STATUS_WORD = re.compile(r"(?i)\b(done|completed?|finished)\b")
MARKETING = re.compile(r"(?i)\b(blazing(ly)?|powerful|seamless(ly)?|revolutionary|cutting-edge|game-?changing|"
                       r"next-gen(eration)?|world-class|supercharge)\b")


def _lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def check_readme(ctx, lines):
    found = []
    text = [(i + 1, l) for i, l in enumerate(lines) if l.strip()]
    if not text or not text[0][1].startswith("# "):
        found.append(ctx.finding(NAME, "README.md:1", "first line must be `# <name>`"))
    else:
        rest = [l for _, l in text[1:6] if not l.lstrip().startswith(("[![", "![", "<"))]
        if not rest or not rest[0].startswith("**"):
            found.append(ctx.finding(NAME, "README.md", "a **bold one-line summary** must follow the title"))
    last = -1
    section = ""
    for no, line in text:
        if line.startswith("## "):
            head = line[3:].strip().lower()
            section = head
            idx = next((i for i, key in enumerate(ORDER) if head.startswith(key)), None)
            if idx is not None:
                if idx < last:
                    found.append(ctx.finding(NAME, "README.md:%d" % no, "section out of order (expected: %s)" % ORDER_TEXT, line))
                last = max(last, idx)
        if DOCS_LINK.search(line):
            found.append(ctx.finding(NAME, "README.md:%d" % no, "links to docs/, which is never uploaded", line))
        if section == "status" and STATUS_WORD.search(line):
            found.append(ctx.finding(NAME, "README.md:%d" % no, "status word: use Tested, Working or Planned", line))
        m = MARKETING.search(line)
        if m:
            found.append(ctx.finding(NAME, "README.md:%d" % no, "marketing word: %s" % m.group(0), line))
    warn = [i for i, (_, l) in enumerate(text) if l.strip().lower().startswith("> [!warning]")]
    if warn and any(not l.lstrip().startswith(">") for _, l in text[warn[-1]:]):
        found.append(ctx.finding(NAME, "README.md:%d" % text[warn[-1]][0], "the warning box must be the last thing"))
    return found


def run(ctx):
    found = []
    for rel in NEEDED:
        if rel not in ctx.files:
            found.append(ctx.finding(NAME, rel, "missing (needed: README.md and LICENSE)"))
    lines = _lines(ctx.path("README.md")) if "README.md" in ctx.files else None
    if lines is not None:
        found += check_readme(ctx, lines)
    for rel in ctx.files:
        if rel.startswith("docs/") and rel.endswith(".md"):
            for no, line in enumerate(_lines(ctx.path(rel)) or [], 1):
                if "TEMPLATE:" in line:
                    found.append(ctx.finding(NAME, "%s:%d" % (rel, no), "template guide text left over", line))
    return found
