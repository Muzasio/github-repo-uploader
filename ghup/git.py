"""Small git wrapper. Argument lists only, never a shell."""
import os
import subprocess
import tempfile


class GitError(Exception):
    pass


def make_env(extra=None):
    """Environment for git: never prompt in the terminal, plain C messages, plus `extra`."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    env.update(extra or {})
    return env


def run(args, cwd, env=None, check=True, data=None):
    """Run `git <args>` and return stdout as text."""
    try:
        res = subprocess.run(["git", *args], cwd=cwd, env=make_env(env), input=data, capture_output=True,
                             text=data is None or isinstance(data, str), timeout=300)
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise GitError("git %s took too long" % args[0]) from None
    if check and res.returncode != 0:
        err = res.stderr if isinstance(res.stderr, str) else res.stderr.decode("utf-8", "replace")
        raise GitError("git %s failed: %s" % (args[0], err.strip()[:300]))
    return res.stdout


def is_repo(project):
    return os.path.isdir(os.path.join(project, ".git"))


def upload_list(project, planned_excludes=()):
    """Files git would upload, honoring .gitignore and .git/info/exclude, also before `git init`.

    planned_excludes: names the pipeline will put in .git/info/exclude at `git init` (used before it exists).
    """
    if is_repo(project):
        out = run(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], project)
    else:
        # an empty bare repo pointed at the folder makes git apply .gitignore
        with tempfile.TemporaryDirectory() as tmp:
            run(["init", "--quiet", "--bare", tmp], project)
            out = run(["--git-dir=" + tmp, "--work-tree=" + project, "ls-files", "-z", "--others",
                       "--exclude-standard", *["--exclude=/" + p for p in planned_excludes]], project)
    return sorted({f for f in out.split("\0") if f})


def has_commits(project):
    return is_repo(project) and bool(run(["rev-parse", "--verify", "-q", "HEAD"], project, check=False).strip())


def commit_messages(project):
    """(short sha, message) for every commit on every branch."""
    if not has_commits(project):
        return []
    out = run(["log", "--all", "--format=%h%x00%B%x00"], project)
    parts = out.split("\0")
    return [(parts[i].strip(), parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def commit_emails(project):
    if not has_commits(project):
        return []
    return sorted(set(run(["log", "--all", "--format=%ae%n%ce"], project).split()))


def history_blobs(project, max_bytes):
    """Yield (path, bytes) for every file version in the history that is not the version in the index.

    Old versions count: a file deleted later is still on GitHub in the history.
    """
    if not has_commits(project):
        return
    current = set()
    for line in run(["ls-files", "-s", "-z"], project).split("\0"):
        if line:
            current.add(line.split()[1])
    seen = {}
    for line in run(["rev-list", "--objects", "--all"], project).splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in current and sha not in seen:
            seen[sha] = path
    if not seen:
        return
    shas = list(seen)
    out = run(["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], project,
              data="\n".join(shas) + "\n")
    blobs = []
    for line in out.splitlines():
        sha, kind, size = line.split()
        if kind == "blob":
            blobs.append((sha, int(size)))
    for sha, size in blobs:
        if size > max_bytes:
            yield seen[sha], None
            continue
        data = run(["cat-file", "blob", sha], project, data=b"")
        yield seen[sha], data


# --- used by the pipeline ---------------------------------------------------------------------------

def init(project, branch, excludes=()):
    """git init with the given branch name, and add `excludes` to .git/info/exclude (never uploaded)."""
    run(["init", "--quiet", "-b", branch], project)
    path = os.path.join(project, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            have = {line.strip() for line in f}
    except FileNotFoundError:
        have = set()
    with open(path, "a", encoding="utf-8") as f:
        for name in excludes:
            if name not in have and "/" + name not in have:
                f.write(name + "\n")


def current_branch(project):
    return run(["symbolic-ref", "--quiet", "--short", "HEAD"], project, check=False).strip()


def valid_branch(name):
    res = subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True, text=True)
    return res.returncode == 0 and not name.startswith("-")


def add_all(project):
    run(["add", "--all", "--", "."], project)


def staged_files(project):
    return sorted({f for f in run(["ls-files", "-z", "--cached"], project).split("\0") if f})


def file_ids(project, rels):
    """{path: git blob id} of the files as they are now (what `git add` would store). Symlinks: their target."""
    ids, plain = {}, []
    for rel in rels:
        path = os.path.join(project, rel)
        if os.path.islink(path):
            ids[rel] = "link:" + os.readlink(path)
        else:
            plain.append(rel)
    if plain:
        out = run(["hash-object", "--stdin-paths"], project, data="\n".join(plain) + "\n").split()
        ids.update(zip(plain, out))
    return ids


def staged_ids(project):
    """{path: blob id} of what is staged now; symlinks as "link:<target>" (like file_ids)."""
    ids = {}
    for entry in run(["ls-files", "-s", "-z"], project).split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            mode, sha = meta.split()[:2]
            ids[path] = "link:" + run(["cat-file", "blob", sha], project) if mode == "120000" else sha
    return ids


def status_short(project):
    return run(["status", "--short", "--untracked-files=all"], project).splitlines()


def has_staged_changes(project):
    if not has_commits(project):
        return bool(staged_files(project))
    return subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project, env=make_env()).returncode == 1


def remote_url(project, name="origin"):
    if not is_repo(project):
        return ""
    return run(["remote", "get-url", name], project, check=False).strip()


def set_remote(project, url, name="origin"):
    if remote_url(project, name):
        run(["remote", "set-url", name, url], project)
    else:
        run(["remote", "add", name, url], project)


def remote_heads(url, cwd, env=None):
    """{branch: sha} on the remote, or None if the repo does not exist or cannot be reached."""
    res = subprocess.run(["git", "ls-remote", "--heads", "--", url], cwd=cwd, env=make_env(env),
                         capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        return None
    heads = {}
    for line in res.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def contains(project, sha):
    """True if the local history already has this commit (so a push is a fast-forward)."""
    if not has_commits(project):
        return False
    if subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"], cwd=project, env=make_env(),
                      capture_output=True).returncode != 0:
        return False
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=project, env=make_env(),
                          capture_output=True).returncode == 0


def commit(project, message, name, email):
    """Commit what is staged, with this author and committer only (overrides git config and env)."""
    who = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
           "GIT_COMMITTER_EMAIL": email}
    run(["-c", "user.name=" + name, "-c", "user.email=" + email, "commit", "--quiet", "--file=-"],
        project, env=who, data=message)
    return rev(project, "HEAD")


def rev(project, ref):
    return run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], project, check=False).strip()


def commits_ahead(project, remote_ref):
    """Number of local commits the remote branch does not have."""
    if not rev(project, remote_ref):
        return int(run(["rev-list", "--count", "HEAD"], project).strip() or 0)
    return int(run(["rev-list", "--count", remote_ref + "..HEAD"], project).strip() or 0)


def push(project, branch, env=None):
    """Push the branch and set its upstream. Never --force."""
    run(["push", "--set-upstream", "origin", "refs/heads/%s:refs/heads/%s" % (branch, branch)], project, env=env)


def fetch(project, env=None):
    run(["fetch", "--quiet", "origin"], project, env=env)


def tree_files(project, ref):
    return sorted(f for f in run(["ls-tree", "-r", "-z", "--name-only", ref], project).split("\0") if f)
