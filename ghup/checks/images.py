"""Images: text checks cannot read pictures, so every image must be opened by hand.

Also reads image metadata with exiftool (if installed) and fails on personal values or GPS in it.
"""
import json
import shutil
import subprocess

from . import IMAGES

NAME = "images"
TITLE = "Images opened by hand, metadata clean"

# metadata tags that often hold a name, a path, a place or a PC name
RISKY_TAGS = ("gps", "author", "artist", "creator", "owner", "copyright", "comment", "usercomment",
              "hostcomputer", "documentname", "xpauthor", "xpcomment", "byline", "credit", "rights", "by-line")


def metadata(path):
    """Tag -> value from exiftool, or None if exiftool is missing or failed."""
    # Arch puts exiftool in /usr/bin/vendor_perl, which is not always on PATH
    exe = shutil.which("exiftool") or shutil.which("exiftool", path="/usr/bin/vendor_perl")
    if not exe:
        return None
    try:
        res = subprocess.run([exe, "-j", "-G1", "-a", "-n", "--", path], capture_output=True, text=True, timeout=60)
        return json.loads(res.stdout)[0] if res.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def run(ctx):
    found = []
    images = [rel for rel in ctx.files if rel.lower().endswith(IMAGES)]
    old = sorted({line for where, kind, line in ctx.texts()
                  if kind == "name" and where.startswith("history") and line.lower().endswith(IMAGES)})
    for rel in images:
        found.append(ctx.finding(NAME, rel, "open it and look for dev-log links, local paths, names, private notes",
                                 level="confirm"))
        tags = metadata(ctx.path(rel))
        if tags is None:
            found.append(ctx.finding(NAME, rel, "metadata not checked (exiftool missing or failed)", level="confirm"))
            continue
        for tag, value in tags.items():
            if tag == "SourceFile" or tag.startswith(("System:", "ExifTool:")):
                continue
            text = str(value)
            hits = ctx.personal_hits(text, rel)
            if hits:
                found.append(ctx.finding(NAME, rel, "metadata %s: %s" % (tag, ", ".join(hits)), text))
            elif tag.split(":")[-1].lower().startswith(RISKY_TAGS) and text.strip():
                found.append(ctx.finding(NAME, rel, "metadata %s: check this value" % tag, text, level="confirm"))
    for rel in old:
        found.append(ctx.finding(NAME, "history: " + rel, "old image version in the git history: check it too",
                                 level="confirm"))
    return found
