"""CLI wiring: preview must never publish on its own."""
import io
import json
import pathlib
import unittest
from contextlib import redirect_stdout

from thread2social.cli import main

FIXTURE = pathlib.Path(__file__).parent / "fixtures/nthngdy_raw.json"


class TestPreview(unittest.TestCase):
    def setUp(self):
        """A Thread JSON on disk, so no network and no credentials are involved."""
        from thread2social.model import Thread
        from thread2social.read_syndication import check, walk
        raw = json.loads(FIXTURE.read_text())
        chain = walk("2090073048565072360", "2090073045146677693",
                     fetcher=lambda i: raw[i], pause=0)
        warnings = check(chain, "2090073045146677693", allow_incomplete=False)
        self.path = pathlib.Path("/tmp/t2s_test_thread.json")
        self.path.write_text(Thread(chain[0].author, chain,
                                    source_url=chain[0].url,
                                    warnings=warnings).to_json())

    def test_preview_is_the_default_and_publishes_nothing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--from-json", str(self.path), "--to", "bluesky"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Preview only", out)          # non-TTY: no publish prompt
        self.assertIn("9 post(s)", out)
        self.assertIn("\U0001F501 @nthngdy", out)   # opening credit, short form

    def test_preview_shows_both_targets_and_the_warnings(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--from-json", str(self.path)])
        out = buf.getvalue()
        self.assertIn("bluesky: 9 post(s)", out)
        self.assertIn("mastodon: 8 post(s)", out)
        self.assertIn("crossposted from @nthngdy", out)
        self.assertIn("[warn]", out)

    def test_no_urls_and_no_json_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
