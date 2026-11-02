"""Tests for the pipeline. The "GitHub" remote is a local bare repo, so nothing leaves the machine."""
import os
import subprocess
import unittest

from test_checks import NEEDLES, Base, git

from ghup import pipeline
from ghup.config import Config


class ScriptedUI:
    """Answers questions from a list; records everything it was shown."""

    def __init__(self, confirms=None, message="Add the demo tool\n\n- does one thing\n"):
        self.confirms = list(confirms) if confirms is not None else None
        self.message = message
        self.shown = []

    def step(self, number, title):
        self.shown.append("step %d %s" % (number, title))

    def info(self, text):
        self.shown.append(text)

    def findings(self, title, found):
        self.shown += ["%s: %s" % (f.where, f.message) for f in found]

    def files(self, lines):
        self.shown += list(lines)

    def confirm(self, question):
        self.shown.append(question)
        return True if self.confirms is None else self.confirms.pop(0)

    def ask(self, prompt):
        return self.message


class PipelineBase(Base):
    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        self.cfg = Config(github_name="octo-cat", email="1+octo-cat@users.noreply.github.com",
                          devlog_dir=os.path.join(self.tmp.name, "devlog"))

    def upload(self, ui=None, dry_run=False, remote=None, **kw):
        self.ui = ui or ScriptedUI()
        p = pipeline.Pipeline(self.project, self.cfg, self.ui, dry_run=dry_run, ssh_env={},
                              remote_url=remote or self.remote, needles=NEEDLES, **kw)
        return p.run()

    def remote_files(self):
        out = subprocess.run(["git", "--git-dir", self.remote, "ls-tree", "-r", "--name-only", "main"],
                             capture_output=True, text=True)
        return sorted(out.stdout.split())


class DryRun(PipelineBase):
    def test_changes_nothing(self):
        self.write("CLAUDE.md", "private\n")
        res = self.upload(dry_run=True)
        self.assertTrue(res.ok, res)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".git")))
        self.assertEqual(self.remote_files(), [])
        self.assertNotIn("CLAUDE.md", self.ui.shown)  # planned exclude: not in the upload list


class FullRun(PipelineBase):
    def test_upload_then_nothing_new(self):
        self.write("CLAUDE.md", "private\n")
        res = self.upload()
        self.assertTrue(res.ok, res)
        self.assertNotIn("CLAUDE.md", self.remote_files())
        self.assertIn("README.md", self.remote_files())
        author = subprocess.run(["git", "--git-dir", self.remote, "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(author, "octo-cat <1+octo-cat@users.noreply.github.com>|"
                                 "octo-cat <1+octo-cat@users.noreply.github.com>")
        with open(os.path.join(self.project, ".git/info/exclude")) as f:
            self.assertIn("CLAUDE.md", f.read().split())
        # second run: nothing new, still OK
        res = self.upload()
        self.assertTrue(res.ok, res)
        self.assertIn("nothing new to commit", self.ui.shown)
        self.assertIn("nothing new to push", self.ui.shown)

    def test_second_commit(self):
        self.assertTrue(self.upload().ok)
        self.write("docs/USER-GUIDE.md", "# USER-GUIDE\n\nMore.\n")
        res = self.upload(ui=ScriptedUI(message="Explain the main command"))
        self.assertTrue(res.ok, res)
        self.assertTrue(res.commit)


class GitignoreOffer(PipelineBase):
    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.project, ".gitignore"))
        self.write("run.log", "x\n")

    def test_accepted(self):
        self.assertTrue(self.upload().ok)
        with open(os.path.join(self.project, ".gitignore")) as f:
            self.assertIn("*.log", f.read().split())
        self.assertNotIn("run.log", self.remote_files())
        self.assertIn(".gitignore", self.remote_files())

    def test_declined(self):
        res = self.upload(ui=ScriptedUI(confirms=[False]))
        self.assertEqual(res.stopped_at, "Checks")
        self.assertFalse(os.path.exists(os.path.join(self.project, ".gitignore")))

    def test_dry_run_writes_nothing(self):
        self.upload(dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".gitignore")))
        self.assertTrue(any("would offer" in line for line in self.ui.shown))


class Stops(PipelineBase):
    def assertStopped(self, res, step, text=""):
        self.assertFalse(res.ok)
        self.assertEqual(res.stopped_at, step, res)
        self.assertIn(text, res.reason)

    def test_failing_check_changes_nothing(self):
        self.write("app.py", "PATH = '%s/x'\n" % [k for k, v in NEEDLES.items() if v == "home folder"][0])
        self.assertStopped(self.upload(), "Checks", "problem")
        self.assertFalse(os.path.exists(os.path.join(self.project, ".git")))

    def test_image_not_confirmed(self):
        self.write("shot.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 16, mode="wb")
        self.assertStopped(self.upload(ui=ScriptedUI(confirms=[False])), "Checks", "not confirmed")

    def test_files_changed_after_checks(self):
        ui = ScriptedUI()
        ui.findings = lambda title, found: self.write("sneaky.txt", "added during the checks\n")
        self.assertStopped(self.upload(ui=ui), "Review", "changed after the checks")

    def test_file_edited_after_checks(self):
        ui = ScriptedUI()
        ui.findings = lambda title, found: self.write("hello.py", "print('changed')\n")
        self.write("hello.py", "print('hi')\n")
        self.assertStopped(self.upload(ui=ui), "Review", "hello.py")

    def test_review_declined(self):
        self.assertStopped(self.upload(ui=ScriptedUI(confirms=[False])), "Review", "not approved")

    def test_push_declined(self):
        self.assertStopped(self.upload(ui=ScriptedUI(confirms=[True, False])), "Push", "cancelled")
        self.assertEqual(self.remote_files(), [])

    def test_repo_missing(self):
        self.assertStopped(self.upload(remote=os.path.join(self.tmp.name, "nope.git")), "Repo", "not found")

    def test_remote_has_other_commits(self):
        other = os.path.join(self.tmp.name, "other")
        subprocess.run(["git", "clone", "-q", self.remote, other], check=True, capture_output=True)
        with open(os.path.join(other, "x.txt"), "w") as f:
            f.write("x\n")
        git(other, "add", ".")
        git(other, "commit", "-qm", "other")
        git(other, "push", "-q", "origin", "HEAD:main")
        self.assertStopped(self.upload(), "Repo", "pull them first")

    def test_origin_elsewhere(self):
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "remote", "add", "origin", "https://example.com/x.git")
        self.assertStopped(self.upload(), "Repo", "somewhere else")

    def test_bad_messages(self):
        for msg, text in (("wip", "say what"), ("Add it\n\nCo-" + "Authored-By: Claude <a@example.com>", "credit"),
                          ("Fix path for alice", "personal"), ("x" * 80, "72")):
            res = self.upload(ui=ScriptedUI(message=msg))
            self.assertStopped(res, "Commit", text)

    def test_home_and_parent_folders(self):
        self.assertStopped(pipeline.Pipeline(os.path.expanduser("~"), self.cfg, ScriptedUI(), dry_run=True,
                                             needles=NEEDLES).run(), "Pick", "home")
        git(os.path.join(self.project, "docs"), "init", "-q")
        self.assertStopped(self.upload(dry_run=True), "Pick", "other git repos")

    def test_devlog_folder_refused(self):
        self.cfg.devlog_dir = self.tmp.name  # the project is inside it
        self.assertStopped(self.upload(dry_run=True), "Pick", "dev-log")

    def test_real_github_push_needs_key(self):
        p = pipeline.Pipeline(self.project, self.cfg, ScriptedUI(), needles=NEEDLES)
        p.ssh_env = None
        p.url = "git@github.com:octo-cat/demo.git"
        p.pick()
        p.run_checks()
        p.review()
        with self.assertRaises(pipeline.Stop):
            p.push()


if __name__ == "__main__":
    unittest.main()
