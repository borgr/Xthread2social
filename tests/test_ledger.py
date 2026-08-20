"""The ledger's job: resume mid-thread, per target, without reposting."""
import tempfile
import unittest
from pathlib import Path

from thread2social.ledger import Ledger


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "ledger.json"

    def test_unknown_thread_starts_at_zero(self):
        self.assertEqual(Ledger(self.tmp).done("123", "bluesky"), (0, []))

    def test_records_and_reloads_per_target(self):
        led = Ledger(self.tmp)
        led.record("123", "bluesky", 5, [("at://a", "cid1"), ("at://b", "cid2")])
        self.assertEqual(Ledger(self.tmp).done("123", "bluesky")[0], 5)
        self.assertEqual(Ledger(self.tmp).done("123", "mastodon"), (0, []))

    def test_partial_failure_of_one_target_leaves_the_other_resumable(self):
        led = Ledger(self.tmp)
        led.record("123", "bluesky", 9, [("at://x", "c")])
        led.record("123", "mastodon", 4, [("11", "u")])
        self.assertEqual(led.done("123", "bluesky")[0], 9)
        self.assertEqual(led.done("123", "mastodon")[0], 4)

    def test_refs_round_trip_as_tuples(self):
        Ledger(self.tmp).record("1", "bluesky", 1, [("at://a", "cid")])
        _, refs = Ledger(self.tmp).done("1", "bluesky")
        self.assertEqual(refs, [("at://a", "cid")])


if __name__ == "__main__":
    unittest.main()
