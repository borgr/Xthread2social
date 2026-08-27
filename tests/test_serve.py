"""Listener routing: auth is checked before anything else, and bad input is a 4xx."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from xthread2social import config, serve


def _isolate_log(case):
    """Point the listener log at a temp file. Without this the suite appends test traffic
    (403s, deliberate 400s) to the real ~/.local/share log and poisons a later diagnosis."""
    d = tempfile.TemporaryDirectory()
    case.addCleanup(d.cleanup)
    old = serve.LOG
    serve.LOG = Path(d.name) / "serve.log"
    case.addCleanup(lambda: setattr(serve, "LOG", old))
    return serve.LOG


class TestHandle(unittest.TestCase):
    def setUp(self):
        os.environ["LISTENER_TOKEN"] = "tok"
        _isolate_log(self)

    def test_missing_token_is_rejected_before_any_work(self):
        status, body = serve.handle("POST", "/publish", {}, b'{"urls":["1"]}')
        self.assertEqual(status, 403)
        self.assertIn("X-Token", body["error"])

    def test_ping_needs_the_token_too(self):
        self.assertEqual(serve.handle("GET", "/ping", {"x-token": "tok"}, b"")[0], 200)
        self.assertEqual(serve.handle("GET", "/ping", {"x-token": "no"}, b"")[0], 403)

    def test_unknown_route_and_bad_json(self):
        self.assertEqual(serve.handle("POST", "/nope", {"x-token": "tok"}, b"{}")[0], 404)
        self.assertEqual(serve.handle("POST", "/preview", {"x-token": "tok"}, b"{oops")[0], 400)

    def test_no_urls_is_a_client_error_not_a_crash(self):
        status, body = serve.handle("POST", "/preview", {"x-token": "tok"}, b"{}")
        self.assertEqual(status, 400)
        self.assertIn("no tweet urls", body["error"])

    def test_listener_without_a_token_configured_refuses_everything(self):
        os.environ["LISTENER_TOKEN"] = ""
        old, config.keychain = config.keychain, lambda *a, **k: ""
        try:
            status, body = serve.handle("GET", "/ping", {"x-token": "tok"}, b"")
        finally:
            config.keychain = old
        self.assertEqual(status, 503)
        self.assertIn("--install-listener", body["error"])


class TestReadRequest(unittest.TestCase):
    def test_parses_method_path_headers_and_body(self):
        import io
        raw = (b"POST /preview?x=1 HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Token: t\r\n"
               b"Content-Length: 13\r\n\r\n{\"urls\": [1]}")
        method, path, headers, body = serve.read_request(io.BytesIO(raw))
        self.assertEqual((method, path), ("POST", "/preview"))
        self.assertEqual(headers["x-token"], "t")
        self.assertEqual(json.loads(body), {"urls": [1]})


if __name__ == "__main__":
    unittest.main()


class TestListenerInstall(unittest.TestCase):
    def test_serve_binary_must_exist(self):
        """A plist pointing at a missing binary loads and then resets every connection."""
        import shutil
        from pathlib import Path
        from xthread2social import listener
        found = shutil.which("xthread2social-serve")
        if found:
            self.assertTrue(Path(listener.serve_binary()).exists())
        else:
            with self.assertRaises(RuntimeError):
                listener.serve_binary()

    def test_plist_binds_loopback_only(self):
        from xthread2social import listener
        try:
            sock = listener.plist_body()["Sockets"]["Listener"]
        except RuntimeError:
            self.skipTest("xthread2social-serve not installed")
        self.assertEqual(sock["SockNodeName"], "127.0.0.1")
        self.assertEqual(sock["SockFamily"], "IPv4")


class TestLog(unittest.TestCase):
    def test_lines_are_timestamped_and_the_file_stays_bounded(self):
        log = _isolate_log(self)
        serve.log("hello")
        self.assertRegex(log.read_text(), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d hello$")
        log.write_bytes(b"x\n" * serve.MAX_LOG)
        serve.log("after")
        self.assertLess(log.stat().st_size, serve.MAX_LOG)
        self.assertIn("after", log.read_text())

    def test_a_log_write_failure_never_breaks_a_request(self):
        _isolate_log(self)
        serve.LOG = Path("/nonexistent-dir-xthread2social/serve.log")
        serve.log("dropped")                      # must not raise


class TestIncompleteIsAQuestion(unittest.TestCase):
    """A tail with replies must come back as a confirmable question, not a dead error."""

    def setUp(self):
        _isolate_log(self)

    def test_needs_confirm_instead_of_400(self):
        from xthread2social import read_syndication as rs

        def fake(payload):
            raise rs.IncompleteError("7 repl(y/ies)")

        old, serve.ROUTES["/preview"] = serve.ROUTES["/preview"], fake
        os.environ["LISTENER_TOKEN"] = "tok"
        try:
            status, body = serve.handle("POST", "/preview", {"x-token": "tok"}, b"{}")
        finally:
            serve.ROUTES["/preview"] = old
        self.assertEqual(status, 200)
        self.assertIn("7 repl", body["needs_confirm"])
        self.assertNotIn("error", body)

    def test_a_real_read_failure_is_still_an_error(self):
        from xthread2social import read_syndication as rs

        def fake(payload):
            raise rs.ReadError("not a tweet URL")

        old, serve.ROUTES["/preview"] = serve.ROUTES["/preview"], fake
        try:
            status, body = serve.handle("POST", "/preview", {"x-token": "tok"}, b"{}")
        finally:
            serve.ROUTES["/preview"] = old
        self.assertEqual(status, 400)
        self.assertIn("not a tweet URL", body["error"])


class TestPreviewLabelsSplits(unittest.TestCase):
    """One long tweet becomes several posts; the payload must say which tweet they came from,
    or the overlay's "1 tweet -> 2 posts" looks like a stray tweet was picked up."""

    def test_parts_are_numbered_per_tweet(self):
        _isolate_log(self)
        from xthread2social import serve as sv
        from xthread2social.model import Thread, Tweet
        long = Tweet("11", "x " * 400, "me")
        old, sv._thread = sv._thread, lambda payload: Thread("me", [long], source_url="u")
        try:
            body = sv.route_preview({"to": ["bluesky"], "urls": ["11"]})
        finally:
            sv._thread = old
        rows = body["targets"]["bluesky"]
        self.assertGreater(len(rows), 1)
        self.assertEqual(body["tweets"], 1)
        self.assertEqual([r["part"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(r["parts"] == len(rows) and r["tweet"] == 1 for r in rows))


class TestPostAsMine(unittest.TestCase):
    """Someone else's thread, carried under your own name because its author asked.

    Off by default on every path. The overlay only offers it when there is a credit to drop,
    which is what `credited` in the preview payload is for.
    """

    def setUp(self):
        _isolate_log(self)
        self._saved = os.environ.get("X_HANDLES")
        os.environ["X_HANDLES"] = "me"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("X_HANDLES", None)
        else:
            os.environ["X_HANDLES"] = self._saved

    def _preview(self, author, **payload):
        from xthread2social import serve as sv
        from xthread2social.model import Thread, Tweet
        t = Thread(author, [Tweet("11", "their words", author)],
                   source_url=f"https://x.com/{author}/status/11")
        old, sv._thread = sv._thread, lambda p: t
        try:
            return sv.route_preview({"to": ["bluesky"], "urls": ["11"], **payload})
        finally:
            sv._thread = old

    def test_a_foreign_thread_is_flagged_as_creditable(self):
        body = self._preview("someone")
        self.assertFalse(body["mine"])
        self.assertTrue(body["credited"])
        self.assertIn("@someone", body["targets"]["bluesky"][0]["text"])

    def test_asking_for_no_attribution_removes_every_trace_of_the_author(self):
        body = self._preview("someone", no_attribution=True)
        text = " ".join(r["text"] for r in body["targets"]["bluesky"])
        self.assertNotIn("someone", text)
        self.assertNotIn("x.com", text)

    def test_my_own_thread_offers_nothing_to_drop(self):
        body = self._preview("me")
        self.assertTrue(body["mine"])
        self.assertFalse(body["credited"])

    def test_the_default_still_credits(self):
        self.assertIn("@someone", self._preview("someone")["targets"]["bluesky"][0]["text"])


class TestOverlayMatchesTheApi(unittest.TestCase):
    """The overlay is the only caller of these two routes, and it ships from a raw URL rather
    than from this package, so a rename here has to be visible in the .js as well."""

    def setUp(self):
        self.js = (Path(__file__).resolve().parent.parent
                   / "userscript/xthread2social.user.js").read_text()

    def test_both_routes_send_the_no_attribution_flag(self):
        for route in ("/preview", "/publish"):
            call = self.js.split(f"call('{route}'", 1)[1][:300]
            self.assertIn("no_attribution", call, f"{route} does not send it")

    def test_the_control_is_offered_only_when_there_is_a_credit_to_drop(self):
        self.assertIn("data.credited ?", self.js)

    def test_the_control_starts_unchecked_so_the_default_cannot_drift(self):
        box = self.js.split('id="x2s-mine"', 1)[0].rsplit("<label", 1)[1] + 'id="x2s-mine"'
        self.assertNotIn("checked", box)
