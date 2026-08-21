"""The note: your own words on the first post, and the cache that makes editing it cheap."""
import tempfile
import unittest
from pathlib import Path

from xthread2social.model import Thread, Tweet
from xthread2social.targets import render
from xthread2social.textutil import glen


def thread(first="opener", second="second"):
    return Thread("someone", [Tweet("1", first, "someone"), Tweet("2", second, "someone")],
                  source_url="https://x.com/someone/status/1")


class TestNote(unittest.TestCase):
    def test_note_goes_above_the_authors_text_on_the_first_post_only(self):
        units = render(thread(), "mastodon", note="why this matters")
        self.assertTrue(units[0]["text"].startswith("why this matters\n\nopener"))
        self.assertNotIn("why this matters", units[1]["text"])

    def test_blank_and_whitespace_notes_change_nothing(self):
        plain = render(thread(), "mastodon")
        for note in ("", "   ", "\n\n"):
            self.assertEqual([u["text"] for u in render(thread(), "mastodon", note=note)],
                             [u["text"] for u in plain])

    def test_a_long_note_pushes_the_original_down_instead_of_truncating_it(self):
        units = render(thread(), "bluesky", note="n " * 200)
        self.assertGreater(len(units), 2)
        self.assertIn("opener", " ".join(u["text"] for u in units))
        for u in units:
            self.assertLessEqual(glen(u["text"]), 300)

    def test_note_and_credit_coexist_on_the_first_post(self):
        units = render(thread(), "bluesky", note="my take",
                       credit=["\n\n\U0001F501 x-post from @someone"])
        self.assertTrue(units[0]["text"].startswith("my take"))
        self.assertIn("x-post from @someone", units[0]["text"])


class TestCache(unittest.TestCase):
    """Editing the note re-previews; that must not re-walk the chain against X."""

    def setUp(self):
        from xthread2social import cache
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.cache, old = cache, cache.DIR
        cache.DIR = Path(d.name)
        self.addCleanup(lambda: setattr(cache, "DIR", old))

    def test_round_trip_keeps_tweets_and_warnings(self):
        t = Thread("me", [Tweet("1", "hi", "me")], source_url="u", warnings=["careful"])
        self.cache.put(["1"], True, t)
        got = self.cache.get(["1"], True)
        self.assertEqual(got.tweets[0].text, "hi")
        self.assertEqual(got.warnings, ["careful"])
        self.assertIsInstance(got.tweets[0], Tweet)

    def test_a_different_id_set_or_gate_is_a_miss(self):
        self.cache.put(["1"], True, thread())
        self.assertIsNone(self.cache.get(["1", "2"], True))
        self.assertIsNone(self.cache.get(["1"], False))

    def test_an_expired_or_corrupt_entry_is_a_miss_not_a_crash(self):
        import os
        import time
        self.cache.put(["1"], True, thread())
        f = next(self.cache.DIR.glob("*.json"))
        os.utime(f, (time.time() - self.cache.TTL - 5,) * 2)
        self.assertIsNone(self.cache.get(["1"], True))
        self.cache.put(["1"], True, thread())
        next(self.cache.DIR.glob("*.json")).write_text("{not json")
        self.assertIsNone(self.cache.get(["1"], True))


class TestServePassesTheNote(unittest.TestCase):
    def test_preview_renders_with_the_note(self):
        from xthread2social import serve as sv
        old, sv._thread = sv._thread, lambda payload: thread()
        try:
            body = sv.route_preview({"to": ["mastodon"], "urls": ["1"], "note": "hello"})
        finally:
            sv._thread = old
        self.assertTrue(body["targets"]["mastodon"][0]["text"].startswith("hello"))


if __name__ == "__main__":
    unittest.main()
