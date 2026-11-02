"""The upload pipeline. The CLI and the GUI are thin front ends over this.

    1 pick → 2 checks → 3 review → 4 repo → 5 commit → 6 push → 7 verify

The pipeline talks to the user only through a `ui` object:

    ui.step(number, title)             a step starts
    ui.info(text)                      a message
    ui.findings(title, findings)       one check's results
    ui.files(lines)                    the exact upload list
    ui.confirm(question) -> bool       yes/no; the default answer is no
    ui.ask(prompt) -> str              free text ("" = cancel)

A dry run changes nothing: no git init, no commit, no push.
"""
import os
import re
from dataclasses import dataclass, field

from . import checks, git
from .checks import credit_lines, gitignore, personal_info, private_notes, repo_name
from .config import is_inside, private_dirs

STEPS = ["Pick", "Checks", "Review", "Repo", "Commit", "Push", "Verify"]
PLANNED_EXCLUDES = ("CLAUDE.md",)
BAD_MESSAGES = re.compile(r"(?i)^(initial commit|first commit|wip|update|updates|changes|commit|test|\.)$")


class Stop(Exception):
    """The pipeline stops here; nothing after this step runs."""


@dataclass
class Result:
    ok: bool = False
    dry_run: bool = False
    stopped_at: str = ""
    reason: str = ""
    commit: str = ""
    warnings: list = field(default_factory=list)  # dry run: what would stop a real run
    log: list = field(default_factory=list)     # (step title, status, note)


class Pipeline:
    def __init__(self, project, cfg, ui, repo_name=None, branch=None, message=None, dry_run=False,
                 ssh_env=None, remote_url=None, needles=None):
        """ssh_env: environment that makes git use the tool's own SSH key (step 4 of the build); None = no key.
        remote_url, needles: only for tests (a local bare repo, fake personal values).
        Normal runs always use git@github.com:<name>/<repo>.git and this machine's values.
        """
        self.needles = needles
        self.project = os.path.realpath(project)
        self.cfg = cfg
        self.ui = ui
        self.repo_name = repo_name or os.path.basename(self.project)
        self.branch = branch
        self.message = message
        self.dry_run = dry_run
        self.ssh_env = ssh_env
        self.url = remote_url or "git@github.com:%s/%s.git" % (cfg.github_name, self.repo_name)
        self.result = Result(dry_run=dry_run)
        self.ctx = None
        self.confirmed = 0
        self.ids = {}

    # --- driver ------------------------------------------------------------------------------------

    def run(self):
        stages = [self.pick, self.run_checks, self.review, self.repo, self.commit, self.push, self.verify]
        for number, (title, stage) in enumerate(zip(STEPS, stages), 1):
            self.ui.step(number, title)
            try:
                note = stage() or ""
            except Stop as e:
                self._log(title, "stopped", str(e))
                self.result.stopped_at, self.result.reason = title, str(e)
                return self.result
            except (git.GitError, OSError, ValueError) as e:
                self._log(title, "error", str(e))
                self.result.stopped_at, self.result.reason = title, "error: %s" % e
                return self.result
            self._log(title, "dry run" if self.dry_run and number >= 3 else "done", note)
            if note:
                self.ui.info(note)
        self.result.ok = not self.result.warnings
        return self.result

    def _log(self, title, status, note):
        self.result.log.append((title, status, note))

    # --- 1 pick ------------------------------------------------------------------------------------

    def pick(self):
        p = self.project
        home = os.path.realpath(os.path.expanduser("~"))
        if not os.path.isdir(p):
            raise Stop("not a folder")
        if p == "/" or is_inside(home, p):
            raise Stop("this is your home folder or above it, not a project folder")
        for folder in private_dirs():
            if is_inside(p, folder) or is_inside(folder, p):
                raise Stop("this overlaps the tool's own private config/state folder")
        if self.cfg.devlog_dir and (is_inside(p, self.cfg.devlog_dir) or is_inside(self.cfg.devlog_dir, p)):
            raise Stop("this overlaps your private dev-log folder")
        nested = self._nested_repos(p)
        if nested:
            raise Stop("this folder holds other git repos (%s): pick one project folder" % ", ".join(nested[:3]))
        if not self.branch:
            self.branch = git.current_branch(p) if git.is_repo(p) else "main"
        if not self.branch or not git.valid_branch(self.branch):
            raise Stop("no valid branch: check out a branch first")
        return "project folder: %s, branch %s" % (os.path.basename(p), self.branch)

    @staticmethod
    def _nested_repos(project, depth=3):
        found = []
        for root, dirs, _ in os.walk(project):
            rel = os.path.relpath(root, project)
            level = 0 if rel == "." else rel.count(os.sep) + 1
            if level and ".git" in dirs:
                found.append(rel)
            dirs[:] = [d for d in dirs if d != ".git" and not d.startswith((".venv", "node_modules"))
                       and level < depth]
        return sorted(found)

    # --- 2 checks ----------------------------------------------------------------------------------

    def _context(self):
        self.ctx = checks.Context(self.project, self.repo_name, self.cfg, needles=self.needles,
                                  planned_excludes=PLANNED_EXCLUDES)
        # content ids before the checks read anything: review stops if a file changes after this
        self.ids = git.file_ids(self.project, self.ctx.files)

    def run_checks(self):
        self._context()
        add = gitignore.suggest(self.ctx)
        if add and self.dry_run:
            self.ui.info("a real run would offer to add to .gitignore: %s" % ", ".join(add))
        elif add and self.ui.confirm("Add to .gitignore: %s?" % ", ".join(add)):
            gitignore.append(self.ctx, add)
            self.ui.info(".gitignore updated")
            self._context()
        fails = 0
        to_confirm = []
        for mod, found in checks.run_all(self.ctx, skip=self.cfg.skip_checks):
            self.ui.findings(mod.TITLE, found)
            fails += sum(f.level == "fail" for f in found)
            to_confirm += [f for f in found if f.level == "confirm"]
        if fails:
            raise Stop("%d problem(s) to fix first" % fails)
        for f in to_confirm:
            if not self.ui.confirm("%s: %s. Checked by hand and safe?" % (f.where, f.message)):
                raise Stop("not confirmed: %s" % f.where)
        self.confirmed = len(to_confirm)
        return "%d files, all checks passed%s" % (
            len(self.ctx.files), ", %d confirmed by hand" % self.confirmed if self.confirmed else "")

    # --- 3 review ----------------------------------------------------------------------------------

    def review(self):
        if self.dry_run:
            self.ui.files(self.ctx.files)
            return "would upload these %d files (git init + add, not done in a dry run)" % len(self.ctx.files)
        if not git.is_repo(self.project):
            git.init(self.project, self.branch, PLANNED_EXCLUDES)
            self.ui.info("git init done (branch %s); %s added to .git/info/exclude" % (
                self.branch, ", ".join(PLANNED_EXCLUDES)))
        git.add_all(self.project)
        staged = git.staged_ids(self.project)
        changed = sorted(set(staged.items()) ^ set(self.ids.items()))
        if changed:
            names = sorted({path for path, _ in changed})
            raise Stop("the files changed after the checks (%s): run again" % ", ".join(names[:5]))
        self.ui.files(git.status_short(self.project) or ["(no changes since the last commit)"])
        if not self.ui.confirm("Upload exactly these %d files?" % len(staged)):
            raise Stop("not approved")
        return "%d files approved" % len(staged)

    # --- 4 repo ------------------------------------------------------------------------------------

    def repo(self):
        bad = repo_name.run(self.ctx)
        if bad:
            raise Stop("bad repo name: %s" % bad[0].message)
        current = git.remote_url(self.project)
        if current and current != self.url:
            raise Stop("origin already points somewhere else; fix it by hand (git remote -v)")
        heads = self._remote_heads()
        shown = "%s/%s" % (self.cfg.github_name, self.repo_name)
        if heads is None and self.dry_run:
            self.result.warnings.append("%s not found on GitHub" % shown)
            return "a real run would stop here: %s not found (create it on github.com/new, empty)" % shown
        if heads is None:
            raise Stop("%s not found. Create it on github.com/new: empty, no README, license or .gitignore" % shown)
        sha = heads.get(self.branch)
        if sha and not git.contains(self.project, sha):
            raise Stop("GitHub has commits on %s that you don't have: pull them first (never force)" % self.branch)
        return "%s exists (%s)" % (shown, "empty" if not heads else "has branch %s" % self.branch if sha
                                   else "no branch %s yet" % self.branch)

    def _remote_heads(self):
        if self.ssh_env is not None or not self.url.startswith("git@github.com:"):
            return git.remote_heads(self.url, self.project, self.ssh_env)
        # no SSH key yet (or dry run): a read-only check over HTTPS works for public repos
        heads = git.remote_heads("https://github.com/%s/%s.git" % (self.cfg.github_name, self.repo_name),
                                 self.project)
        if heads is None and self.dry_run:
            self.ui.info("not found over HTTPS: the repo is missing or private (needs the SSH key)")
        return heads

    # --- 5 commit ----------------------------------------------------------------------------------

    def commit(self):
        who = "%s <%s>" % (self.cfg.github_name, self.cfg.email)
        message = self.message or ("" if self.dry_run else self.ui.ask("Commit message (summary line first)"))
        if message:
            self._check_message(message)
        elif not self.dry_run:
            raise Stop("no commit message")
        if self.dry_run:
            return "would commit as %s%s" % (who, ", message OK" if message else "")
        if not git.has_staged_changes(self.project):
            return "nothing new to commit"
        self.result.commit = git.commit(self.project, message.strip() + "\n", self.cfg.github_name, self.cfg.email)
        return "commit %s as %s" % (self.result.commit[:7], who)

    def _check_message(self, message):
        first = message.strip().splitlines()[0].strip() if message.strip() else ""
        if not first or len(first) > 72:
            raise Stop("the summary line must be 1 to 72 characters")
        if BAD_MESSAGES.match(first):
            raise Stop("say what the commit does, not \"%s\"" % first)
        for line in message.splitlines():
            if credit_lines.CREDIT.search(line) and not self.ctx.credit_allowed():
                raise Stop("the message has a credit line")
            if self.ctx.personal_hits(line) or any(rx.search(line) for _, rx in personal_info.PATTERNS):
                raise Stop("the message has personal info")
            if any(rx.search(line) for _, rx in private_notes.NOTES + private_notes.config_notes(self.cfg)):
                raise Stop("the message has a private note")

    # --- 6 push ------------------------------------------------------------------------------------

    def push(self):
        if self.dry_run:
            return "would push branch %s to %s (never --force)" % (self.branch, self._shown_url())
        if self.ssh_env is None and self.url.startswith("git@github.com:"):
            raise Stop("no SSH key for the tool yet: pushing is not possible")
        ahead = git.commits_ahead(self.project, "origin/" + self.branch)
        if ahead == 0:
            return "nothing new to push"
        if not git.remote_url(self.project):
            git.set_remote(self.project, self.url)
        if not self.ui.confirm("Push %d commit(s) on %s to %s?" % (ahead, self.branch, self._shown_url())):
            raise Stop("push cancelled")
        git.push(self.project, self.branch, self.ssh_env)
        return "pushed %s" % self.branch

    def _shown_url(self):
        return "%s/%s" % (self.cfg.github_name, self.repo_name) if self.url.startswith("git@github.com:") \
            else "the test remote"

    # --- 7 verify ----------------------------------------------------------------------------------

    def verify(self):
        if self.dry_run:
            return "would compare local and GitHub after the push"
        git.fetch(self.project, self.ssh_env)
        remote = "origin/" + self.branch
        local_sha, remote_sha = git.rev(self.project, "HEAD"), git.rev(self.project, remote)
        if local_sha != remote_sha:
            raise Stop("GitHub does not match: local %s, GitHub %s" % (local_sha[:7], remote_sha[:7] or "none"))
        if git.tree_files(self.project, remote) != git.tree_files(self.project, "HEAD"):
            raise Stop("GitHub's file list differs from the local one")
        if git.status_short(self.project):
            self.ui.info("note: there are local changes that were not uploaded")
        return "GitHub matches local (%s, %d files)" % (local_sha[:7], len(git.tree_files(self.project, remote)))
