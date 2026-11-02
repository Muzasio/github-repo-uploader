"""Tests for config.py: folder and file modes, validation, round trip."""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghup import config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            old = os.environ.get(var)
            os.environ[var] = os.path.join(self.tmp.name, var.lower())
            self.addCleanup(lambda v=var, o=old: os.environ.pop(v) if o is None else os.environ.__setitem__(v, o))
        self.cfg = config.Config(github_name="octo-cat", email="123+octo-cat@users.noreply.github.com",
                                 devlog_dir=os.path.join(self.tmp.name, "log"))

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_round_trip_and_modes(self):
        config.save(self.cfg)
        self.assertEqual(config.load(), self.cfg)
        self.assertEqual(self.mode(config.config_file()), 0o600)
        for d in (config.config_dir(), config.state_dir(), os.path.join(config.config_dir(), "ssh")):
            self.assertEqual(self.mode(d), 0o700)

    def test_refuses_readable_file(self):
        config.save(self.cfg)
        os.chmod(config.config_file(), 0o644)
        with self.assertRaises(config.ConfigError):
            config.load()

    def test_refuses_symlink(self):
        config.ensure_dirs()
        target = os.path.join(self.tmp.name, "other.toml")
        with open(target, "w") as f:
            f.write(self.cfg.to_toml())
        os.chmod(target, 0o600)
        os.symlink(target, config.config_file())
        with self.assertRaises(config.ConfigError):
            config.load()

    def test_bad_values(self):
        for change in ({"github_name": "-bad"}, {"email": "me@" + "gmail.com"}, {"rotate_days": 0},
                       {"devlog_dir": "relative/path"}, {"skip_checks": ["Bad Name"]}, {"key_created": "soon"}):
            cfg = config.Config(**{**self.cfg.__dict__, **change})
            with self.assertRaises(config.ConfigError, msg=change):
                cfg.validate()

    def test_unknown_key(self):
        config.save(self.cfg)
        with open(config.config_file(), "a") as f:
            f.write('token = "x"\n')
        with self.assertRaises(config.ConfigError):
            config.load()

    def test_quotes_escaped(self):
        self.cfg.devlog_dir = os.path.join(self.tmp.name, 'a "b" \\c')
        config.save(self.cfg)
        self.assertEqual(config.load().devlog_dir, self.cfg.devlog_dir)


if __name__ == "__main__":
    unittest.main()
