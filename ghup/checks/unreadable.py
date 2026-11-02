"""Files the text checks cannot read (binary, or over 5 MB): a PDF, zip or database can hold personal info too,
so each one must be opened by hand. Images are asked about by the images check."""
import os

from . import IMAGES, MAX_BYTES

NAME = "unreadable"
TITLE = "Files the checks cannot read, opened by hand"


def run(ctx):
    found = []
    for rel in ctx.files:
        path = ctx.path(rel)
        if rel.lower().endswith(IMAGES) or os.path.islink(path):
            continue
        try:
            if os.path.getsize(path) > MAX_BYTES:
                why = "over 5 MB"
            else:
                with open(path, "rb") as f:
                    why = "binary" if b"\0" in f.read(8192) else ""
        except OSError:
            why = "cannot be opened"
        if why:
            found.append(ctx.finding(NAME, rel, "not scanned (%s): open it and check by hand" % why, level="confirm"))
    return found
