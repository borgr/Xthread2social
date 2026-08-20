"""Credential resolution: env file for names, Keychain for secrets."""
import os
import tempfile
import unittest
from pathlib import Path

from thread2social import config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("ATPROTO_HANDLE", "ATPROTO_APP_PASSWORD", "MASTODON_ACCESS_TOKEN")}
        for k in self.saved:
            os.environ.pop(k, None)
        self.real_keychain = config.keychain

    def tearDown(self):
        config.keychain = self.real_keychain
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write(self, text):
        p = Path(tempfile.mkdtemp()) / "env"
        p.write_text(text)
        return p

    def test_parses_values_and_strips_inline_comments(self):
        p = self.write("ATPROTO_HANDLE=me.bsky.social  # my handle\n\n# comment\n")
        config.keychain = lambda name, service=None: ""
        self.assertEqual(config.load(p)["ATPROTO_HANDLE"], "me.bsky.social")

    def test_keychain_fills_the_literal_placeholder(self):
        p = self.write("ATPROTO_APP_PASSWORD=keychain\n")
        config.keychain = lambda name, service=None: "from-keychain" if "APP" in name else ""
        config.load(p)
        self.assertEqual(config.get("ATPROTO_APP_PASSWORD"), "from-keychain")

    def test_keychain_fills_a_missing_secret_too(self):
        p = self.write("ATPROTO_HANDLE=me.bsky.social\n")
        config.keychain = lambda name, service=None: "tok" if "MASTODON" in name else ""
        config.load(p)
        self.assertEqual(config.get("MASTODON_ACCESS_TOKEN"), "tok")

    def test_a_real_value_in_the_file_is_not_overridden(self):
        p = self.write("ATPROTO_APP_PASSWORD=in-file\n")
        config.keychain = lambda name, service=None: "from-keychain"
        config.load(p)
        self.assertEqual(config.get("ATPROTO_APP_PASSWORD"), "in-file")

    def test_missing_keychain_entry_is_not_fatal(self):
        p = self.write("ATPROTO_APP_PASSWORD=keychain\n")
        config.keychain = lambda name, service=None: ""
        config.load(p)
        self.assertEqual(config.get("ATPROTO_APP_PASSWORD"), "keychain")


if __name__ == "__main__":
    unittest.main()


class TestStore(unittest.TestCase):
    def test_refuses_an_empty_secret(self):
        with self.assertRaises(ValueError):
            config.store("ATPROTO_APP_PASSWORD", "")

    def test_round_trips_through_the_real_keychain(self):
        import subprocess
        name = "THREAD2SOCIAL_TEST_SECRET"
        try:
            config.store(name, "abcd-1234", service="thread2social-test")
            self.assertEqual(config.keychain(name, "thread2social-test"), "abcd-1234")
        finally:
            subprocess.run(["security", "delete-generic-password", "-s",
                            "thread2social-test", "-a", name],
                           capture_output=True)
