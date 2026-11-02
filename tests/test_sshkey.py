"""Tests for sshkey.py with a real ssh-keygen and ssh-agent in a temp folder. No network, no ~/.ssh."""
import datetime
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghup import sshkey  # noqa: E402
from ghup.config import Config  # noqa: E402


class SSHKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {"XDG_CONFIG_HOME": os.path.join(self.tmp.name, "c"), "XDG_STATE_HOME": os.path.join(self.tmp.name, "s"),
               "SSH_ASKPASS": os.path.join(self.tmp.name, "askpass"), "SSH_ASKPASS_REQUIRE": "force"}
        for var, value in env.items():
            old = os.environ.get(var)
            os.environ[var] = value
            self.addCleanup(lambda v=var, o=old: os.environ.pop(v) if o is None else os.environ.__setitem__(v, o))
        self.answer("right-passphrase")
        sshkey.ensure_dirs()
        self.key = sshkey.key_path()
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "right-passphrase", "-C", "test", "-f", self.key],
                       check=True)
        # stop the test's agent afterwards (the socket path is unique to this test)
        self.addCleanup(subprocess.run, ["pkill", "-f", "ssh-agent -a " + sshkey.sock_path()])
        self.cfg = Config(github_name="octo-cat", email="1+octo-cat@users.noreply.github.com",
                          key_created=datetime.date.today().isoformat())

    def answer(self, passphrase):
        """The askpass program ssh-add runs instead of asking on a terminal. It answers once, then fails
        (= cancel), like a user who gives up after a wrong passphrase."""
        path = os.environ["SSH_ASKPASS"]
        done = path + ".done"
        if os.path.exists(done):
            os.remove(done)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n[ -e '%s' ] && exit 1\ntouch '%s'\necho '%s'\n" % (done, done, passphrase))
        os.chmod(path, 0o700)

    def test_unlock_and_lock(self):
        self.assertFalse(sshkey.unlocked(self.key))
        sshkey.unlock(self.key, 1)
        self.assertTrue(sshkey.unlocked(self.key))
        sshkey.lock()
        self.assertFalse(sshkey.unlocked(self.key))

    def test_wrong_passphrase(self):
        self.answer("wrong")
        with self.assertRaises(sshkey.SSHKeyError):
            sshkey.unlock(self.key, 1)

    def test_git_env_pins_github(self):
        cmd = sshkey.git_env(self.key)["GIT_SSH_COMMAND"]
        for part in ("IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519",
                     "IdentityAgent=" + sshkey.sock_path(), "-F /dev/null", "Port=443"):
            self.assertIn(part, cmd)
        known = os.path.join(os.path.dirname(self.key), "known_hosts")
        with open(known) as f:
            self.assertEqual(f.read(), sshkey.GITHUB_HOST_KEY)
        self.assertEqual(stat.S_IMODE(os.stat(known).st_mode), 0o600)

    def test_ready_env(self):
        env = sshkey.ready_env(self.cfg)  # unlocks through askpass
        self.assertIn("GIT_SSH_COMMAND", env)
        self.cfg.key_created = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        with self.assertRaisesRegex(sshkey.SSHKeyError, "rotate"):
            sshkey.ready_env(self.cfg)
        self.cfg.key_created = ""
        with self.assertRaisesRegex(sshkey.SSHKeyError, "key new"):
            sshkey.ready_env(self.cfg)

    def test_stale_socket(self):
        sshkey.unlock(self.key, 1)
        subprocess.run(["pkill", "-f", "ssh-agent -a " + sshkey.sock_path()])
        self.answer("right-passphrase")
        sshkey.unlock(self.key, 1)  # agent dead, socket file left: starts a new one
        self.assertTrue(sshkey.unlocked(self.key))


if __name__ == "__main__":
    unittest.main()
