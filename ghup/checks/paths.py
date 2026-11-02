"""Safe paths: no symlinks that point out of the project, no files from the tool's own private folders."""
import os

from ..config import is_inside, private_dirs

NAME = "paths"
TITLE = "No symlinks out of the project, no private folders"


def run(ctx):
    found = []
    for folder in private_dirs():
        if is_inside(folder, ctx.project) or is_inside(ctx.project, folder):
            found.append(ctx.finding(NAME, "project folder", "overlaps the tool's private config/state folder"))
    for rel in ctx.files:
        path = ctx.path(rel)
        if os.path.islink(path):
            target = os.path.realpath(path)
            if not is_inside(target, ctx.project):
                found.append(ctx.finding(NAME, rel, "symlink points outside the project", os.readlink(path)))
    return found
