"""Rendering: splitting, image placement, attribution, grapheme safety."""
import json
import pathlib
import unittest

from thread2social.model import Media, Thread, Tweet
from thread2social.read_syndication import walk
from thread2social.targets import render
from thread2social.textutil import chunk_for_bluesky, glen

RAW = json.loads((pathlib.Path(__file__).parent / "fixtures/nthngdy_raw.json").read_text())
CHAIN = walk("2090073048565072360", "2090073045146677693", fetcher=lambda i: RAW[i], pause=0)
THREAD = Thread(CHAIN[0].author, CHAIN, source_url=CHAIN[0].url)


class TestRender(unittest.TestCase):
    def test_every_tweet_appears_and_order_is_kept(self):
        units = render(THREAD, "mastodon")
        self.assertEqual(len(units), 8)
        self.assertEqual([u["tweet"].id for u in units], [t.id for t in CHAIN])

    def test_images_ride_the_first_post_of_their_tweet(self):
        units = render(THREAD, "bluesky")
        self.assertEqual(sum(len(u["images"]) for u in units), 8)
        for u in units:
            if u["images"]:
                first = next(x for x in units if x["tweet"].id == u["tweet"].id)
                self.assertIs(u, first)

    def test_bluesky_posts_fit_the_grapheme_cap(self):
        for u in render(THREAD, "bluesky"):
            self.assertLessEqual(glen(u["text"]), 300, u["text"])

    def test_mastodon_posts_fit_500(self):
        for u in render(THREAD, "mastodon"):
            self.assertLessEqual(len(u["text"]), 500)

    def test_attribution_lands_only_on_the_final_post(self):
        units = render(THREAD, "mastodon", attribution="\n\n— via @x")
        self.assertIn("— via @x", units[-1]["text"])
        self.assertEqual(sum("— via @x" in u["text"] for u in units), 1)

    def test_quoted_tweet_is_inlined(self):
        t = Tweet("1", "look at this", "me", quoted="@other: original words")
        units = render(Thread("me", [t]), "mastodon")
        self.assertIn("> @other: original words", units[0]["text"])

    def test_long_tweet_splits_into_several_posts(self):
        t = Tweet("1", "word " * 200, "me", media=[Media("http://x/a.jpg")])
        units = render(Thread("me", [t]), "bluesky")
        self.assertGreater(len(units), 1)
        self.assertEqual(len(units[0]["images"]), 1)
        self.assertEqual(units[1]["images"], [])       # images only on the first


class TestGraphemes(unittest.TestCase):
    def test_emoji_dense_text_still_fits(self):
        text = "👨‍👩‍👧‍👦 " * 80                       # 7 codepoints, 1 grapheme each
        for c in chunk_for_bluesky(text):
            self.assertLessEqual(glen(c), 300)

    def test_glen_counts_family_emoji_as_one(self):
        self.assertEqual(glen("👨‍👩‍👧‍👦"), 1)


if __name__ == "__main__":
    unittest.main()
