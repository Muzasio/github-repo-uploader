"""Repo hygiene: a .gitignore, CLAUDE.md kept out through .git/info/exclude (not .gitignore), no clutter."""
import os
import re

NAME = "gitignore"
TITLE = ".gitignore, CLAUDE.md excluded, no clutter"

CLUTTER = re.compile(r"(^|/)(__pycache__/|node_modules/|\.venv/|venv/|\.pytest_cache/|\.mypy_cache/|"
                     r"[^/]*\.(pyc|pyo|bak|swp|swo|orig|rej|log|tmp)$|[^/]*~$|\.DS_Store$|Thumbs\.db$)")
# a new .gitignore starts with these (offered, written only after a yes)
DEFAULT = ["__pycache__/", "*.pyc", ".venv/", "venv/", "node_modules/", ".env", "*.log", "*.bak", "*.swp", "*~",
           ".DS_Store"]


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def suggest(ctx):
    """Lines to add to .gitignore: the defaults if it is missing, else a pattern per kind of clutter found."""
    if ".gitignore" not in ctx.files:
        return list(DEFAULT)
    lines = []
    for rel in ctx.files:
        m = CLUTTER.search(rel)
        if m:
            name, ext = m.group(2), m.group(3)  # group 3: the junk extension (pyc, log, ...)
            line = "*." + ext if ext else "*~" if name.endswith("~") else name
            if line not in lines:
                lines.append(line)
    return lines


def append(ctx, lines):
    with open(ctx.path(".gitignore"), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run(ctx):
    found = []
    if ".gitignore" not in ctx.files:
        found.append(ctx.finding(NAME, ".gitignore", "missing: add one before the first commit"))
    elif re.search(r"(?im)^\s*/?CLAUDE\.md\s*$", _read(ctx.path(".gitignore"))):
        found.append(ctx.finding(NAME, ".gitignore", "names CLAUDE.md: move it to .git/info/exclude"))
    if os.path.exists(ctx.path("CLAUDE.md")) and not (not ctx.is_repo and "CLAUDE.md" in ctx.planned_excludes):
        exclude = _read(ctx.path(".git/info/exclude")) if ctx.is_repo else ""
        if not re.search(r"(?im)^\s*/?CLAUDE\.md\s*$", exclude):
            found.append(ctx.finding(NAME, "CLAUDE.md", "add `CLAUDE.md` to .git/info/exclude (after git init)"))
    for rel in ctx.files:
        if CLUTTER.search(rel):
            found.append(ctx.finding(NAME, rel, "clutter: add it to .gitignore"))
        elif os.path.basename(rel) == ".gitkeep":
            found.append(ctx.finding(NAME, rel, "placeholder file: not needed if the program makes the folder",
                                     level="confirm"))
    return found
