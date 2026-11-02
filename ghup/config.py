"""Local config and state folders. Nothing personal lives in the repo: it is all here, outside it.

    ~/.config/github-uploader/            700
    ├── config.toml                       600
    └── ssh/                              700   the tool's own key (sshkey.py)
    ~/.local/state/github-uploader/       700   agent socket, logs/
"""
import datetime
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field

APP = "github-uploader"

NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
NOREPLY_RE = re.compile(r"^(\d+\+)?[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})@users\.noreply\.github\.com$")
CHECK_NAME_RE = re.compile(r"^[a-z_]{1,40}$")


class ConfigError(Exception):
    pass


def _xdg(var, default):
    value = os.environ.get(var, "")
    # the XDG spec says relative values are invalid and must be ignored
    return value if os.path.isabs(value) else os.path.expanduser(default)


def config_dir():
    return os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), APP)


def state_dir():
    return os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state"), APP)


def config_file():
    return os.path.join(config_dir(), "config.toml")


def private_dirs():
    """Folders the tool must never upload from."""
    return [config_dir(), state_dir()]


def ensure_private_dir(path):
    """Create a folder with mode 700 (and fix it if it exists). Refuse a symlink."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ConfigError("not a real folder: %s" % path)
    if st.st_uid != os.getuid():
        raise ConfigError("folder is owned by another user: %s" % path)
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def ensure_dirs():
    for path in (config_dir(), os.path.join(config_dir(), "ssh"), state_dir(), os.path.join(state_dir(), "logs")):
        ensure_private_dir(path)


def write_private(path, text):
    """Write a file with mode 600: temp file in the same folder, then rename (no half-written file)."""
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class Config:
    github_name: str = ""
    email: str = ""             # GitHub no-reply address, used as commit author
    devlog_dir: str = ""        # private dev-log folder: never uploaded from or linked to
    rotate_days: int = 7
    unlock_minutes: int = 15
    key_created: str = ""       # ISO date of the tool's SSH key, set by sshkey.py
    skip_checks: list = field(default_factory=list)
    private_words: list = field(default_factory=list)  # extra words that must never be uploaded

    def validate(self):
        if not NAME_RE.match(self.github_name):
            raise ConfigError("github_name is not a valid GitHub user name")
        if not NOREPLY_RE.match(self.email):
            raise ConfigError("email must be a GitHub no-reply address (…@users.noreply.github.com)")
        if self.devlog_dir and not os.path.isabs(self.devlog_dir):
            raise ConfigError("devlog_dir must be an absolute path")
        for key, low, high in (("rotate_days", 1, 90), ("unlock_minutes", 1, 120)):
            value = getattr(self, key)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ConfigError("%s must be a whole number from %d to %d" % (key, low, high))
        if self.key_created:
            try:
                datetime.date.fromisoformat(self.key_created)
            except ValueError:
                raise ConfigError("key_created must be a date like 2026-01-31") from None
        if not isinstance(self.skip_checks, list) or not all(
                isinstance(c, str) and CHECK_NAME_RE.match(c) for c in self.skip_checks):
            raise ConfigError("skip_checks must be a list of check names")
        if not isinstance(self.private_words, list) or not all(
                isinstance(w, str) and 3 <= len(w) <= 100 and w.isprintable() for w in self.private_words):
            raise ConfigError("private_words must be a list of words, 3 to 100 characters each")
        return self

    def to_toml(self):
        # JSON string escapes are valid TOML basic strings
        lines = ["# github-uploader settings. Made by `ghup setup`. Never upload this file."]
        for key in ("github_name", "email", "devlog_dir", "key_created"):
            lines.append("%s = %s" % (key, json.dumps(getattr(self, key))))
        for key in ("rotate_days", "unlock_minutes"):
            lines.append("%s = %d" % (key, getattr(self, key)))
        lines.append("skip_checks = %s" % json.dumps(self.skip_checks))
        lines.append("private_words = %s" % json.dumps(self.private_words))
        return "\n".join(lines) + "\n"


def load(path=None):
    """Read and validate the config. Refuse a file other users can read or change."""
    path = path or config_file()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ConfigError("no config yet: run `ghup setup`") from None
    except OSError as e:
        raise ConfigError("cannot open %s: %s" % (path, e.strerror)) from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise ConfigError("%s must be yours with mode 600 (chmod 600 it)" % path)
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError("config.toml is damaged: %s" % e) from None
    known = Config.__dataclass_fields__
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError("unknown setting(s) in config.toml: %s" % ", ".join(unknown))
    return Config(**data).validate()


def save(cfg, path=None):
    cfg.validate()
    ensure_dirs()
    write_private(path or config_file(), cfg.to_toml())


def is_inside(path, folder):
    """True if path is folder or inside it (both resolved)."""
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return os.path.commonpath([path, folder]) == folder
