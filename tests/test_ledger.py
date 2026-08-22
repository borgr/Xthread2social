"""The ledger's job: resume mid-thread, per target, without reposting."""
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

from xthread2social.ledger import Ledger


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


class TestLock(unittest.TestCase):
    """Two publishes of the same thread at once would each see "nothing posted yet"."""

    def test_the_second_holder_is_refused_not_queued(self):
        import tempfile
        from pathlib import Path
        from xthread2social.ledger import Busy, lock
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ledger.json"
            with lock("42", p):
                with self.assertRaises(Busy):
                    with lock("42", p):
                        pass
                with lock("99", p):               # a different thread is unaffected
                    pass
            with lock("42", p):                   # released on exit
                pass


class TestLockShim(unittest.TestCase):
    """The lock is the one POSIX-only call in the publish path; keep the fallback wired."""

    def test_windows_falls_back_to_the_msvcrt_byte_lock(self):
        import builtins
        from xthread2social import ledger
        real_import, calls = builtins.__import__, []

        def no_fcntl(name, *a, **kw):
            if name == "fcntl":
                raise ImportError("no fcntl on this platform")
            if name == "msvcrt":
                calls.append(name)
                return types.SimpleNamespace(LK_NBLCK=2,
                                             locking=lambda fd, mode, nbytes: calls.append(
                                                 ("locking", mode, nbytes)))
            return real_import(name, *a, **kw)

        with tempfile.TemporaryDirectory() as d:
            with open(Path(d) / "x.lock", "w") as fh:
                with mock.patch.object(builtins, "__import__", no_fcntl):
                    ledger._try_lock(fh)
        self.assertEqual(calls, ["msvcrt", ("locking", 2, 1)])
