"""Check plugins. Each module in this folder is one check:

    NAME = "my_check"           # used in skip_checks
    TITLE = "What it looks for"
    def run(ctx): return [ctx.finding(NAME, where, message, line), ...]

Add a check = drop a file here. A "fail" finding blocks the upload; a "confirm" finding needs a yes from the user.
"""
import getpass
import importlib
import os
import pkgutil
import pwd
import re
import socket
from dataclasses import dataclass

from .. import git
from ..config import NOREPLY_RE

MAX_BYTES = 5 * 1024 * 1024
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".ico", ".avif", ".heic")
SAFE_EMAIL = re.compile(r"(@users\.noreply\.github\.com|^noreply@github\.com|@example\.(com|org|net))$", re.I)
# a personal value right after these is the public GitHub name in a link, not a leak
PUBLIC_CONTEXT = re.compile(r"(github\.com[/:]|githubusercontent\.com/|\.github\.io)$", re.I)
NAME_LABELS = ("username", "git name", "real name")


@dataclass
class Finding:
    check: str
    where: str              # "README.md:3", "history: old.txt:1", "commit 1a2b3c4", "file name"
    message: str
    line: str = ""          # already masked
    level: str = "fail"     # "fail" or "confirm"


def mask(value):
    """Show only the first and last character: "alice" -> "a…e"."""
    value = str(value)
    return value[0] + "…" + value[-1] if len(value) > 2 else "…"


def personal_values():
    """Personal values read from this machine at run time. Short or generic ones are left out (noise)."""
    values = {}
    user = getpass.getuser()
    values[user] = "username"
    values[os.path.expanduser("~")] = "home folder"
    host = socket.gethostname()
    values[host] = values[host.split(".")[0]] = "PC name"
    try:
        real = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except KeyError:
        real = ""
    if real:
        values[real] = "real name"
        values.update({part: "real name" for part in real.split()})
    for key, label in (("user.name", "git name"), ("user.email", "git email")):
        try:
            v = git.run(["config", "--global", key], os.path.expanduser("~"), check=False).strip()
        except git.GitError:
            v = ""
        if v and not SAFE_EMAIL.search(v):
            values[v] = label
    return {v: k for v, k in values.items()
            if len(v) >= 3 and v.lower() not in ("localhost", "root", "user", "home", "/home")}


class Context:
    """What every check sees: the project, the files that would be uploaded, and their text."""

    def __init__(self, project, repo_name=None, config=None, needles=None, planned_excludes=()):
        self.project = os.path.realpath(project)
        self.repo_name = repo_name or os.path.basename(self.project)
        self.config = config
        values = personal_values() if needles is None else dict(needles)
        self.needles = sorted(values.items(), key=lambda kv: -len(kv[0]))
        self.is_repo = git.is_repo(self.project)
        self.planned_excludes = tuple(planned_excludes)
        self.files = git.upload_list(self.project, self.planned_excludes)
        self._texts = None

    def path(self, rel):
        return os.path.join(self.project, rel)

    def credit_allowed(self):
        """True only if the project's CLAUDE.md says `Claude credit: yes`."""
        try:
            with open(self.path("CLAUDE.md"), encoding="utf-8") as f:
                return bool(re.search(r"^Claude credit: yes\s*$", f.read(), re.M))
        except OSError:
            return False

    def texts(self):
        """List of (where, kind, line). kind: "name", "file", "history", "commit". Built once."""
        if self._texts is not None:
            return self._texts
        items = []
        for rel in self.files:
            items.append(("file name: " + rel, "name", rel))
            for no, line in _lines(_read(self.path(rel))):
                items.append(("%s:%d" % (rel, no), "file", line))
        if self.is_repo:
            for rel, data in git.history_blobs(self.project, MAX_BYTES):
                items.append(("history file name: " + rel, "name", rel))
                for no, line in _lines(data):
                    items.append(("history %s:%d" % (rel, no), "history", line))
            for sha, msg in git.commit_messages(self.project):
                for line in msg.splitlines():
                    items.append(("commit " + sha, "commit", line))
            for email in git.commit_emails(self.project):
                items.append(("commit author/committer", "commit", email))
        self._texts = items
        return items

    def personal_hits(self, line, where=""):
        """Labels of personal values found in a line (public GitHub-name uses allowed)."""
        hits = []
        license_copyright = os.path.basename(where).upper().startswith(("LICENSE", "COPYING")) and \
            line.lstrip().lower().startswith("copyright")
        for value, label in self.needles:
            if label in NAME_LABELS and license_copyright:
                continue  # a license must name its author
            for m in re.finditer(re.escape(value), line, re.I):
                if label in NAME_LABELS and PUBLIC_CONTEXT.search(line[:m.start()]):
                    continue
                if label in NAME_LABELS and SAFE_EMAIL.search(line[m.start():].split()[0].rstrip(">\"'`),.;")):
                    continue
                hits.append(label)
                break
        return hits

    def mask_line(self, line):
        """The line with every personal value masked, cut to 140 characters."""
        for value, _ in self.needles:
            line = re.sub(re.escape(value), lambda m: mask(m.group(0)), line, flags=re.I)
        line = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+",
                      lambda m: m.group(0) if SAFE_EMAIL.search(m.group(0)) or NOREPLY_RE.match(m.group(0))
                      else mask(m.group(0)), line)
        line = line.strip()
        return line[:140] + ("…" if len(line) > 140 else "")

    def finding(self, check, where, message, line="", level="fail"):
        return Finding(check, self.mask_line(where), message, self.mask_line(line), level)


def _read(path):
    try:
        if os.path.islink(path) or os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _lines(data):
    if not data or b"\0" in data[:8192]:
        return []  # missing, too big or binary
    return enumerate(data.decode("utf-8", "replace").splitlines(), 1)


def discover():
    """All check modules in this folder, sorted by name."""
    mods = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.name.startswith("_"):
            mod = importlib.import_module("%s.%s" % (__name__, info.name))
            if hasattr(mod, "NAME") and hasattr(mod, "run"):
                mods.append(mod)
    return mods


def run_all(ctx, skip=()):
    """Run every check. Returns [(module, [findings])]. A crashing check is a failure, not a pass."""
    results = []
    for mod in discover():
        if mod.NAME in skip:
            continue
        try:
            found = list(mod.run(ctx))
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
            found = [Finding(mod.NAME, "check itself", "the check crashed: %s" % type(e).__name__)]
        results.append((mod, found))
    return results
