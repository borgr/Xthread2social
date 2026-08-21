"""Reposting your own thread is just posting: no credit, no self-reference, no link to X."""
import os
import unittest
from argparse import Namespace

from xthread2social.cli import attribution_for, credit_for, _my_handles
from xthread2social.read_syndication import Thread, Tweet


def thread(author):
    t = Tweet("1", "my own words", author)
    return Thread(author, [t], source_url=f"https://x.com/{author}/status/1")


def args(**kw):
    return Namespace(**{"no_attribution": False, "source_link": False, **kw})


class TestOwnThread(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("X_HANDLES", "ATPROTO_HANDLE")}
        os.environ["X_HANDLES"] = "me"
        os.environ["ATPROTO_HANDLE"] = "me.bsky.social"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_my_own_thread_carries_nothing(self):
        self.assertEqual(attribution_for(thread("me"), args()), "")
        self.assertEqual(credit_for(thread("me"), args()), "")

    def test_case_does_not_matter(self):
        self.assertEqual(attribution_for(thread("Me"), args()), "")

    def test_source_link_opts_the_url_back_in(self):
        self.assertIn("https://x.com/me/status/1",
                      attribution_for(thread("me"), args(source_link=True)))
        self.assertNotIn("x-post", attribution_for(thread("me"), args(source_link=True)))

    def test_someone_else_is_still_credited_twice(self):
        self.assertIn("— x-post from @other", attribution_for(thread("other"), args()))
        self.assertTrue(credit_for(thread("other"), args()))

    def test_x_handles_wins_over_the_bluesky_fallback(self):
        os.environ["X_HANDLES"] = "@OtherName, second"
        self.assertEqual(_my_handles(), {"othername", "second"})
        self.assertEqual(attribution_for(thread("second"), args()), "")

    def test_bluesky_handle_is_the_fallback_when_x_handles_is_unset(self):
        os.environ.pop("X_HANDLES")
        self.assertEqual(_my_handles(), {"me"})

    def test_no_handles_configured_credits_everyone(self):
        os.environ.pop("X_HANDLES")
        os.environ["ATPROTO_HANDLE"] = ""
        self.assertEqual(_my_handles(), set())
        self.assertIn("x-post from @me", attribution_for(thread("me"), args()))


if __name__ == "__main__":
    unittest.main()
