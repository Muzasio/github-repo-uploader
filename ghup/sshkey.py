"""The tool's own SSH key and ssh-agent. Never touches ~/.ssh or the user's main agent.

    ~/.config/github-uploader/ssh/ghup_ed25519(.pub)   key with passphrase, 600
    ~/.config/github-uploader/ssh/known_hosts          GitHub's ed25519 host key, pinned
    ~/.local/state/github-uploader/agent.sock          the tool's own agent

ssh-keygen and ssh-add ask for the passphrase themselves (terminal, or SSH_ASKPASS), so it never
passes through this program.
"""
import datetime
import os
import re
import shlex
import subprocess

from .config import config_dir, ensure_dirs, state_dir

# from docs.github.com "GitHub's SSH key fingerprints": SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
ADD_KEY_URL = "https://github.com/settings/ssh/new"


class SSHKeyError(Exception):
    pass


def key_path():
    return os.path.join(config_dir(), "ssh", "ghup_ed25519")


def sock_path():
    return os.path.join(state_dir(), "agent.sock")


def known_hosts():
    path = os.path.join(config_dir(), "ssh", "known_hosts")
    with open(path, "w", encoding="utf-8") as f:  # rewritten every time: the pin can't be edited away
        f.write(GITHUB_HOST_KEY)
    os.chmod(path, 0o600)
    return path


def _run(args, env=None, **kw):
    return subprocess.run(args, env=dict(os.environ, **(env or {})), text=True, timeout=kw.pop("timeout", 120), **kw)


def fingerprint(path):
    res = _run(["ssh-keygen", "-l", "-f", path + ".pub"], capture_output=True)
    return res.stdout.split()[1] if res.returncode == 0 else ""


def create(path):
    """Make a new ed25519 key; ssh-keygen asks for the passphrase. An empty passphrase is refused."""
    ensure_dirs()
    comment = "github-uploader %s" % datetime.date.today().isoformat()  # not the default user@host
    if _run(["ssh-keygen", "-q", "-t", "ed25519", "-a", "100", "-C", comment, "-f", path]).returncode != 0:
        raise SSHKeyError("ssh-keygen failed")
    if _run(["ssh-keygen", "-y", "-P", "", "-f", path], capture_output=True).returncode == 0:
        remove(path)
        raise SSHKeyError("a passphrase is required")


def remove(path):
    for p in (path, path + ".pub"):
        if os.path.exists(p):
            os.remove(p)


def _agent(args, **kw):
    return _run(["ssh-add", *args], env={"SSH_AUTH_SOCK": sock_path()}, **kw)


def unlocked(path):
    res = _agent(["-l"], capture_output=True)
    fp = fingerprint(path)
    return bool(fp) and res.returncode == 0 and fp in res.stdout


def unlock(path, minutes):
    """Start the tool's own agent if needed and add the key for `minutes` (ssh-add asks the passphrase)."""
    ensure_dirs()
    if _agent(["-l"], capture_output=True).returncode == 2:  # 2 = no agent on the socket
        if os.path.exists(sock_path()):
            os.remove(sock_path())  # stale socket of a dead agent
        if _run(["ssh-agent", "-a", sock_path()], capture_output=True).returncode != 0:
            raise SSHKeyError("could not start ssh-agent")
    try:
        ok = _agent(["-t", str(minutes * 60), path]).returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    if not ok:
        raise SSHKeyError("unlock failed or cancelled")


def lock():
    """Drop every key from the tool's agent now."""
    _agent(["-D"], capture_output=True)


def git_env(path):
    """Environment that makes git use only this key, only through the tool's agent, only the pinned GitHub."""
    # -F /dev/null: ignore ~/.ssh/config, so no other key or host setting can sneak in.
    # Port 443 (ssh.github.com) works where port 22 is blocked; GitHub uses the same host key there.
    cmd = ["ssh", "-F", "/dev/null", "-o", "Hostname=ssh.github.com", "-o", "Port=443",
           "-o", "HostKeyAlias=github.com", "-i", path, "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=" + sock_path(),
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known_hosts(),
           "-o", "HostKeyAlgorithms=ssh-ed25519"]
    return {"GIT_SSH_COMMAND": " ".join(shlex.quote(a) for a in cmd)}  # git runs it through a shell


def github_user(path):
    """The GitHub account this key logs in as ("" if it doesn't). GitHub answers "Hi <name>! ..." on stderr."""
    ssh = shlex.split(git_env(path)["GIT_SSH_COMMAND"])
    res = _run([*ssh, "-T", "git@github.com"], capture_output=True, timeout=30)
    m = re.match(r"Hi ([\w.-]+)!", res.stderr)
    return m.group(1) if m else ""


def age_days(cfg):
    if not cfg.key_created:
        return None
    return (datetime.date.today() - datetime.date.fromisoformat(cfg.key_created)).days


def ready_env(cfg):
    """git env for a push: the key exists, is younger than rotate_days, and is unlocked (asks if not)."""
    path = key_path()
    if not os.path.exists(path) or not cfg.key_created:
        raise SSHKeyError("no SSH key yet: run `ghup key new`")
    age = age_days(cfg)
    if age >= cfg.rotate_days:
        raise SSHKeyError("the key is %d days old (limit %d): run `ghup key rotate`" % (age, cfg.rotate_days))
    if not unlocked(path):
        unlock(path, cfg.unlock_minutes)
    return git_env(path)
