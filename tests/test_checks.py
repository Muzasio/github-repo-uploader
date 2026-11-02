"""Tests for the check plugins. Only fake values; strings that look personal are built at run time,
so this file passes its own checks."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghup import checks  # noqa: E402

HOME = "/ho" + "me/alice"
NEEDLES = {"alice": "username", "box-42": "PC name", HOME: "home folder", "alice@" + "gmail.com": "git email"}
GOOD_README = """# demo

**Does one thing for one kind of user.**

## What is demo?
Text.

## Features
- **one**: thing

## Quick start
```sh
./install.sh
```

## License
[MIT](LICENSE)

> [!WARNING]
> Not tested on Debian.
"""


def git(project, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], cwd=project, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = os.path.join(self.tmp.name, "demo")
        os.makedirs(self.project)
        # keep the tool's private folders away from the real ones
        for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            old = os.environ.get(var)
            os.environ[var] = os.path.join(self.tmp.name, var.lower())
            self.addCleanup(lambda v=var, o=old: os.environ.pop(v) if o is None else os.environ.__setitem__(v, o))
        self.write(".gitignore", "__pycache__/\n")
        self.write("README.md", GOOD_README)
        self.write("LICENSE", "MIT License\n\nCopyright (c) 2026 alice\n")
        for doc in ("README", "USER-GUIDE", "SETUP", "TROUBLESHOOTING"):
            self.write("docs/%s.md" % doc, "# %s\n" % doc)

    def write(self, rel, text, mode="w"):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as f:
            f.write(text)

    def run_check(self, name, repo_name=None):
        ctx = checks.Context(self.project, repo_name, needles=NEEDLES)
        mod = next(m for m in checks.discover() if m.NAME == name)
        return mod.run(ctx)

    def fails(self, name, **kw):
        return [f for f in self.run_check(name, **kw) if f.level == "fail"]


class CleanProject(Base):
    def test_all_checks_pass(self):
        ctx = checks.Context(self.project, needles=NEEDLES)
        bad = [(m.NAME, f) for m, found in checks.run_all(ctx) for f in found]
        self.assertEqual(bad, [])


class PersonalInfo(Base):
    def test_username_and_home_path(self):
        self.write("app.py", "PATH = '%s/notes'\n" % HOME)
        found = self.fails("personal_info")
        self.assertEqual(len(found), 1)
        self.assertIn("home folder", found[0].message)
        self.assertNotIn("alice", found[0].line)  # masked
        self.assertIn("/…e/notes", found[0].line)

    def test_public_github_link_and_license_allowed(self):
        self.write("README.md", GOOD_README + "\nhttps://github.com/alice/demo\n")
        self.assertEqual(self.fails("personal_info"), [])

    def test_pc_name_ip_email(self):
        self.write("notes.txt", "host box-42 at 192.168." + "1.20\nmail alice@" + "gmail.com\n")
        found = self.fails("personal_info")
        self.assertEqual(len(found), 2)
        self.assertFalse(any("box-42" in f.line or "gmail" in f.line for f in found))

    def test_ip_range_allowed(self):
        self.write("fw.txt", "allow 192.168.0.0/16\n")
        self.assertEqual(self.fails("personal_info"), [])

    def test_deleted_file_still_in_history(self):
        git(self.project, "init", "-q")
        self.write("old.txt", "user alice\n")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "add old")
        os.remove(os.path.join(self.project, "old.txt"))
        git(self.project, "commit", "-qam", "remove old")
        found = self.fails("personal_info")
        self.assertTrue(any(f.where.startswith("history old.txt") for f in found), found)

    def test_commit_email_not_noreply(self):
        git(self.project, "init", "-q")
        git(self.project, "add", ".")
        git(self.project, "-c", "user.email=t@corp.test", "commit", "-qm", "first")
        found = self.fails("personal_info")
        self.assertTrue(any("no-reply" in f.message for f in found))


class Secrets(Base):
    def test_token_hidden(self):
        token = "ghp_" + "A1b2" * 9
        self.write("conf.py", "TOKEN = '%s'\n" % token)
        found = self.fails("secrets")
        self.assertTrue(found)
        self.assertTrue(all(token not in f.line for f in found))

    def test_env_file(self):
        self.write(".env", "X=1\n")
        self.write(".env.example", "X=\n")
        found = self.fails("secrets")
        self.assertEqual([f.line for f in found], [".env"])


class PrivateNotes(Base):
    def test_journey_and_claude_md(self):
        self.write("journey/01-timeline.md", "x\n")
        self.write("CLAUDE.md", "x\n")
        self.write("docs/SETUP.md", "The user " + "asked for this.\n")
        found = self.fails("private_notes")
        self.assertEqual(len(found), 3)


    def test_devlog_folder_and_private_words_from_config(self):
        from ghup.config import Config
        cfg = Config(devlog_dir=os.path.join(os.path.expanduser("~"), "notes-dev"), private_words=["secret-lab"])
        self.write("docs/SETUP.md", "see ~/notes-dev/x\nbuilt in Secret-Lab\n")
        ctx = checks.Context(self.project, config=cfg, needles=NEEDLES)
        mod = next(m for m in checks.discover() if m.NAME == "private_notes")
        self.assertEqual(sorted(f.message for f in mod.run(ctx)), ["dev-log folder", "private word"])


class Credit(Base):
    def test_credit_in_commit_message(self):
        git(self.project, "init", "-q")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "first\n\nCo-" + "Authored-By: Claude <x@example.com>")
        self.assertEqual(len(self.fails("credit_lines")), 1)

    def test_credit_allowed_by_claude_md(self):
        self.write("CLAUDE.md", "## Contributor credit\nClaude credit: yes\n")
        self.write("a.txt", "Generated " + "with Claude\n")
        self.assertEqual(self.fails("credit_lines"), [])


class RepoName(Base):
    def test_names(self):
        self.assertEqual(self.fails("repo_name", repo_name="my-project"), [])
        self.assertTrue(self.fails("repo_name", repo_name="-my-project"))
        self.assertTrue(self.fails("repo_name", repo_name="bad name"))
        self.assertTrue(self.fails("repo_name", repo_name="x.git"))


class GitIgnore(Base):
    def test_claude_md_in_gitignore(self):
        self.write(".gitignore", "CLAUDE.md\n")
        self.write("CLAUDE.md", "x\n")
        self.assertEqual(len(self.fails("gitignore")), 2)

    def test_claude_md_in_exclude(self):
        git(self.project, "init", "-q")
        self.write(".git/info/exclude", "CLAUDE.md\n", mode="a")
        self.write("CLAUDE.md", "x\n")
        self.assertEqual(self.fails("gitignore"), [])

    def test_clutter(self):
        self.write("a.bak", "x\n")
        self.assertEqual(len(self.fails("gitignore")), 1)


class Paths(Base):
    def test_symlink_out(self):
        os.symlink("/etc/hostname", os.path.join(self.project, "link"))
        self.assertEqual(len(self.fails("paths")), 1)


class Readme(Base):
    def test_warning_not_last_and_order(self):
        self.write("README.md", GOOD_README.replace("## Features", "## License\n\n## Features") + "\nMore text.\n")
        msgs = [f.message for f in self.fails("readme_rules")]
        self.assertTrue(any("out of order" in m for m in msgs))
        self.assertTrue(any("warning box" in m for m in msgs))

    def test_marketing_and_missing_license(self):
        self.write("README.md", GOOD_README.replace("Text.", "A powerful tool."))
        os.remove(os.path.join(self.project, "LICENSE"))
        self.assertEqual(len(self.fails("readme_rules")), 2)

    def test_local_docs_not_needed(self):
        for doc in ("README", "USER-GUIDE", "SETUP", "TROUBLESHOOTING"):
            os.remove(os.path.join(self.project, "docs/%s.md" % doc))
        self.assertEqual(self.fails("readme_rules"), [])

    def test_docs_link_and_status_word(self):
        self.write("README.md", GOOD_README.replace("Text.", "See [setup](docs/SETUP.md). ![shot](docs/images/a.png)")
                   + "\n## Status\n\n- **Done**: all of it\n")
        msgs = [f.message for f in self.fails("readme_rules")]
        self.assertEqual(sum("links to docs/" in m for m in msgs), 1)
        self.assertTrue(any("status word" in m for m in msgs))


class Images(Base):
    def test_image_needs_confirm(self):
        self.write("shot.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 16, mode="wb")
        found = self.run_check("images")
        self.assertTrue(found)
        self.assertTrue(all(f.level == "confirm" for f in found))


class Unreadable(Base):
    def test_binary_and_big_files_need_confirm(self):
        self.write("data.db", b"SQLite\0\0\0", mode="wb")
        with open(os.path.join(self.project, "big.txt"), "wb") as f:
            f.truncate(6 * 1024 * 1024)  # over 5 MB
        found = self.run_check("unreadable")
        self.assertEqual(sorted(f.where for f in found), ["big.txt", "data.db"])
        self.assertTrue(all(f.level == "confirm" for f in found))


class Mask(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(checks.mask("alice"), "a…e")
        self.assertEqual(checks.mask("ab"), "…")


if __name__ == "__main__":
    unittest.main()
