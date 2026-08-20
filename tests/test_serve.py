"""Listener routing: auth is checked before anything else, and bad input is a 4xx."""
import json
import os
import unittest

from xthread2social import config, serve


class TestHandle(unittest.TestCase):
    def setUp(self):
        os.environ["LISTENER_TOKEN"] = "tok"

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


class TestIncompleteIsAQuestion(unittest.TestCase):
    """A tail with replies must come back as a confirmable question, not a dead error."""

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
