"""The GitHub repo name: allowed characters, no leading hyphen or dot."""
import re

NAME = "repo_name"
TITLE = "Valid GitHub repo name"

ALLOWED = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def run(ctx):
    name = ctx.repo_name
    problems = []
    if not ALLOWED.match(name):
        problems.append("only letters, digits, '.', '-' and '_' (1 to 100 characters)")
    if name[:1] in "-." or name[-1:] in "-.":
        problems.append("must not start or end with '-' or '.'")
    if name.lower().endswith(".git") or name in (".", ".."):
        problems.append("must not be '.', '..' or end with '.git'")
    if "--" in name:
        problems.append("has '--' (usually a typo)")
    # the name is shown as given (it is public anyway once the repo exists)
    return [ctx.finding(NAME, "repo name", p, name) for p in problems]
