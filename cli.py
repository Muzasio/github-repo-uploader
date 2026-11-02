#!/usr/bin/env python3
"""ghup: check a project folder and upload it to GitHub over SSH.

    ghup setup [--name NAME] [--email EMAIL] [--devlog DIR]   first-run settings (asked if not given)
    ghup check <folder> [--repo-name NAME]                     run every check, upload nothing
    ghup upload <folder> [--dry-run] [--repo-name NAME] [--branch B] [-m MESSAGE]
    ghup key new | rotate | status                             the tool's own SSH key
    ghup lock                                                  forget the unlocked key now
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ghup import __version__, checks, config, git, pipeline, sshkey  # noqa: E402

MARK = {"ok": "✓", "fail": "✗", "confirm": "?"}


def ask(prompt, default=""):
    try:
        answer = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip()
    except EOFError:
        answer = ""
    return answer or default


def guess_from_git():
    """Defaults from git: a no-reply email holds the GitHub name (`123+name@users.noreply.github.com`)."""
    home = os.path.expanduser("~")
    try:
        email = git.run(["config", "--global", "user.email"], home, check=False).strip()
    except git.GitError:
        email = ""
    if config.NOREPLY_RE.match(email):
        return email.split("@")[0].split("+")[-1], email
    return "", ""


def cmd_setup(args):
    try:
        cfg = config.load()
    except config.ConfigError:
        cfg = config.Config()
    name, email = guess_from_git()
    interactive = sys.stdin.isatty()
    cfg.github_name = args.name or (ask("GitHub user name", cfg.github_name or name) if interactive else cfg.github_name or name)
    cfg.email = args.email or (ask("GitHub no-reply email (github.com/settings/emails)", cfg.email or email)
                               if interactive else cfg.email or email)
    devlog = args.devlog if args.devlog is not None else (
        ask("Private dev-log folder: never uploaded or linked (empty = none)", cfg.devlog_dir)
        if interactive else cfg.devlog_dir)
    cfg.devlog_dir = os.path.abspath(os.path.expanduser(devlog)) if devlog else ""
    if interactive and args.devlog is None:
        words = ask("Extra private words to block, comma separated (empty = none)", ", ".join(cfg.private_words))
        cfg.private_words = [w.strip() for w in words.split(",") if w.strip()]
    try:
        config.save(cfg)
    except (config.ConfigError, OSError) as e:
        print("Setup failed: %s" % e, file=sys.stderr)
        return 1
    print("Saved %s (mode 600)." % config.config_file().replace(os.path.expanduser("~"), "~", 1))
    return 0


def cmd_check(args):
    project = os.path.realpath(args.folder)
    if not os.path.isdir(project):
        print("Not a folder: %s" % args.folder, file=sys.stderr)
        return 2
    try:
        cfg = config.load()
    except config.ConfigError:
        cfg = None
    try:
        ctx = checks.Context(project, args.repo_name, cfg)
        results = checks.run_all(ctx, skip=cfg.skip_checks if cfg else ())
    except git.GitError as e:
        print(e, file=sys.stderr)
        return 2
    fails = confirms = 0
    for mod, found in results:
        levels = {f.level for f in found}
        state = "fail" if "fail" in levels else "confirm" if found else "ok"
        print("%s %s" % (MARK[state], mod.TITLE))
        for f in found:
            print("    %s %s  [%s]%s" % (MARK[f.level], f.where, f.message, "  " + f.line if f.line else ""))
            fails += f.level == "fail"
            confirms += f.level == "confirm"
    print("\nChecked %d files. %d to fix, %d to confirm by hand." % (len(ctx.files), fails, confirms))
    return 1 if fails else 0


class TerminalUI:
    """The pipeline's questions and messages, in the terminal. Default answer: no."""

    def step(self, number, title):
        print("\n[%d/%d] %s" % (number, len(pipeline.STEPS), title))

    def info(self, text):
        print("    " + text)

    def findings(self, title, found):
        levels = {f.level for f in found}
        print("    %s %s" % (MARK["fail" if "fail" in levels else "confirm" if found else "ok"], title))
        for f in found:
            print("        %s %s  [%s]%s" % (MARK[f.level], f.where, f.message, "  " + f.line if f.line else ""))

    def files(self, lines):
        for line in lines:
            print("        " + line)

    def confirm(self, question):
        if not sys.stdin.isatty():
            print("    %s -> no (no terminal)" % question)
            return False
        return ask("    %s (y/N)" % question).lower() in ("y", "yes")

    def ask(self, prompt):
        return ask("    " + prompt) if sys.stdin.isatty() else ""


def cmd_upload(args):
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    env = None
    if not args.dry_run:
        try:
            env = sshkey.ready_env(cfg)
        except sshkey.SSHKeyError as e:
            print(e, file=sys.stderr)
            return 2
    p = pipeline.Pipeline(args.folder, cfg, TerminalUI(), repo_name=args.repo_name, branch=args.branch,
                          message=args.message, dry_run=args.dry_run, ssh_env=env)
    res = p.run()
    print()
    if res.stopped_at:
        print("Stopped at %s: %s" % (res.stopped_at, res.reason))
        return 1
    if res.warnings:
        print("Dry run done, but a real run would stop: %s" % "; ".join(res.warnings))
        return 1
    print("Dry run OK: nothing was changed." if res.dry_run else "Upload done.")
    return 0


def cmd_key(args):
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    path = sshkey.key_path()
    if args.action == "status":
        if not os.path.exists(path):
            print("No key yet: run `ghup key new`.")
        else:
            print("Key %s, %s days old (rotate at %d), %s." % (
                sshkey.fingerprint(path), sshkey.age_days(cfg), cfg.rotate_days,
                "unlocked" if sshkey.unlocked(path) else "locked"))
        return 0
    if not sys.stdin.isatty():
        print("This needs a terminal (it asks for a passphrase).", file=sys.stderr)
        return 2
    rotate = args.action == "rotate"
    exists = os.path.exists(path)
    resume = not rotate and exists and not cfg.key_created  # a key made by a `key new` that did not finish
    if rotate != exists and not resume:
        print("No key yet: use `ghup key new`." if rotate else "A key exists: use `ghup key rotate`.",
              file=sys.stderr)
        return 2
    new = path + "-new" if rotate else path
    old_fp = sshkey.fingerprint(path) if rotate else ""
    try:
        if not resume:
            sshkey.remove(new)  # leftover of a cancelled rotation
            sshkey.create(new)
        with open(new + ".pub", encoding="utf-8") as f:
            print("\n1. Open %s\n2. Title: github-uploader, type: Authentication key\n3. Paste this key:\n\n%s"
                  "\nIts fingerprint (GitHub shows it in the key list): %s\n"
                  % (sshkey.ADD_KEY_URL, f.read(), sshkey.fingerprint(new)))
        input("Press Enter when the key is added on GitHub...")
        if not sshkey.unlocked(new):
            sshkey.unlock(new, cfg.unlock_minutes)
        user = sshkey.github_user(new)
        if user.lower() != cfg.github_name.lower():
            raise sshkey.SSHKeyError("GitHub did not accept the key as %s (got: %s)" % (
                cfg.github_name, user or "no login"))
    except (sshkey.SSHKeyError, OSError, EOFError, KeyboardInterrupt) as e:
        if rotate:
            sshkey.remove(new)
        print("\nKey not set up: %s" % (e or "cancelled"), file=sys.stderr)
        return 1
    if rotate:
        sshkey.lock()
        sshkey.remove(path)
        os.replace(new, path)
        os.replace(new + ".pub", path + ".pub")
        sshkey.unlock(path, cfg.unlock_minutes)
        print("Now delete the OLD key on GitHub (%s): https://github.com/settings/keys" % old_fp)
    cfg.key_created = datetime.date.today().isoformat()
    config.save(cfg)
    print("Key works for %s. Unlocked for %d minutes." % (cfg.github_name, cfg.unlock_minutes))
    return 0


def cmd_lock(args):
    sshkey.lock()
    print("Locked.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ghup", description="Check a project and upload it to GitHub over SSH.")
    p.add_argument("--version", action="version", version="ghup " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="first-run settings")
    s.add_argument("--name", help="your GitHub user name")
    s.add_argument("--email", help="your GitHub no-reply email (github.com/settings/emails)")
    s.add_argument("--devlog", metavar="DIR", help="private dev-log folder: never uploaded or linked")
    c = sub.add_parser("check", help="run every check, upload nothing")
    c.add_argument("folder", help="project folder (. = this folder)")
    c.add_argument("--repo-name", help="GitHub repo name (default: the folder name)")
    u = sub.add_parser("upload", help="check, commit and push (asks before each change)")
    u.add_argument("folder", help="project folder (. = this folder)")
    u.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    u.add_argument("--repo-name", help="GitHub repo name (default: the folder name)")
    u.add_argument("--branch", help="branch to push (default: the current one, or main)")
    u.add_argument("-m", "--message", help="commit message (asked if not given)")
    k = sub.add_parser("key", help="the tool's own SSH key")
    k.add_argument("action", choices=["new", "rotate", "status"],
                   help="new: make the key and add it to GitHub; rotate: replace it (every 7 days); status: age, locked?")
    sub.add_parser("lock", help="forget the unlocked key now")
    args = p.parse_args(argv)
    return {"setup": cmd_setup, "check": cmd_check, "upload": cmd_upload, "key": cmd_key,
            "lock": cmd_lock}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
