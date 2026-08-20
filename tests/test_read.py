"""Reader gates and text extraction, offline against the captured fixture."""
import json
import pathlib
import unittest

from thread2social.model import Thread
from thread2social.read_syndication import ReadError, check, parse_ids, to_tweet, walk

RAW = json.loads((pathlib.Path(__file__).parent / "fixtures/nthngdy_raw.json").read_text())
TAIL, ROOT = "2090073048565072360", "2090073045146677693"


def offline(i):
    return RAW[i]


class TestParseIds(unittest.TestCase):
    def test_order_insensitive_and_deduped(self):
        a = f"https://x.com/nthngdy/status/{TAIL}?s=20"
        b = f"https://twitter.com/nthngdy/status/{ROOT}"
        self.assertEqual(parse_ids([a, b]), [ROOT, TAIL])
        self.assertEqual(parse_ids([b, a, b]), [ROOT, TAIL])

    def test_bare_id(self):
        self.assertEqual(parse_ids([ROOT]), [ROOT])

    def test_rejects_junk(self):
        with self.assertRaises(ReadError):
            parse_ids(["https://example.com/not-a-tweet"])


class TestWalk(unittest.TestCase):
    def test_reaches_root_in_order(self):
        chain = walk(TAIL, ROOT, fetcher=offline, pause=0)
        self.assertEqual(len(chain), 8)
        self.assertEqual(chain[0].id, ROOT)
        self.assertEqual(chain[-1].id, TAIL)
        self.assertEqual([t.author for t in chain], ["nthngdy"] * 8)

    def test_walks_to_conversation_root_without_declared_root(self):
        chain = walk(TAIL, "", fetcher=offline, pause=0)
        self.assertEqual(chain[0].id, ROOT)


class TestExtraction(unittest.TestCase):
    def test_media_tco_is_stripped_from_text(self):
        t = to_tweet(RAW[ROOT])
        self.assertNotIn("t.co", t.text)
        self.assertTrue(t.text.endswith("👇"), t.text[-20:])

    def test_html_entities_unescaped(self):
        self.assertIn("& more", to_tweet(RAW[ROOT]).text)
        self.assertNotIn("&amp;", to_tweet(RAW[ROOT]).text)

    def test_links_expanded(self):
        t = to_tweet(RAW["2090073047625543725"])
        self.assertIn("https://arxiv.org/abs/2310.07707", t.text)
        self.assertNotIn("t.co", t.text)

    def test_media_and_counts(self):
        self.assertEqual(len(to_tweet(RAW["2090073048107913325"]).media), 2)
        self.assertEqual(to_tweet(RAW[TAIL]).media, [])
        self.assertEqual(sum(len(to_tweet(d).media) for d in RAW.values()), 8)


class TestGates(unittest.TestCase):
    def test_clean_thread_passes(self):
        chain = walk(TAIL, ROOT, fetcher=offline, pause=0)
        self.assertTrue(any("alt text" in w for w in check(chain, ROOT)))

    def test_wrong_declared_root_is_fatal(self):
        chain = walk(TAIL, ROOT, fetcher=offline, pause=0)
        with self.assertRaises(ReadError) as cm:
            check(chain, "1234567890")
        self.assertIn("incomplete", str(cm.exception))

    def test_gap_in_chain_is_fatal(self):
        chain = walk(TAIL, ROOT, fetcher=offline, pause=0)
        chain[3].reply_to = "999"
        with self.assertRaises(ReadError) as cm:
            check(chain, ROOT)
        self.assertIn("gap", str(cm.exception))

    def test_tail_with_replies_blocks_unless_allowed(self):
        chain = walk("2090073048107913325", "", fetcher=offline, pause=0)  # mid-thread tail
        self.assertTrue(chain[-1].reply_count)
        with self.assertRaises(ReadError):
            check(chain, "")
        self.assertTrue(any("continuation" in w for w in check(chain, "", allow_incomplete=True)))


class TestRoundTrip(unittest.TestCase):
    def test_json_round_trip_preserves_everything(self):
        chain = walk(TAIL, ROOT, fetcher=offline, pause=0)
        th = Thread(chain[0].author, chain, source_url=chain[0].url)
        back = Thread.from_json(th.to_json())
        self.assertEqual([t.text for t in back.tweets], [t.text for t in chain])
        self.assertEqual(back.tweets[6].media[1].url, chain[6].media[1].url)


if __name__ == "__main__":
    unittest.main()
