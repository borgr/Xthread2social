"""Localhost listener so publishing never leaves the browser.

launchd owns the socket (127.0.0.1 only) and starts this handler *on connection* with the
connected socket as stdin/stdout, then reaps it - there is no idle daemon and nothing to
keep alive. The userscript calls it with GM_xmlhttpRequest, which is exempt from x.com's
CSP; a page-context fetch to 127.0.0.1 would be blocked outright.

Requests must carry `X-Token` matching the Keychain-stored LISTENER_TOKEN, so a random
page cannot make you publish. Two routes, deliberately: /preview renders and returns the
posts, /publish writes them. The browser shows the preview and asks before calling /publish.
"""
import json
import os
import shlex
import subprocess
import sys
import traceback
from contextlib import redirect_stdout

from . import config
from .cli import attribution_for, build_targets, credit_for, publish as do_publish
from .ledger import DEFAULT as LEDGER_PATH
from .read_syndication import IncompleteError, ReadError, read_thread
from .targets import render

LOG = LEDGER_PATH.parent / "serve.log"
MAX_BODY = 64 * 1024


class Args:
    """The subset of the CLI's argparse namespace that render/publish read."""
    def __init__(self, to, all_public=False, no_attribution=False):
        self.to = to
        self.all_public = all_public
        self.no_attribution = no_attribution


def _targets_wanted(payload):
    want = payload.get("to") or ["bluesky", "mastodon"]
    return [t for t in want if t in ("bluesky", "mastodon")]


def _thread(payload):
    urls = payload.get("urls") or []
    if not urls:
        raise ReadError("no tweet urls given")
    return read_thread(urls, allow_incomplete=bool(payload.get("allow_incomplete")))


def _web_url(target, ref, handle):
    """Ledger refs -> something clickable. Bluesky stores at-uris, Mastodon stores urls."""
    if target == "mastodon":
        return ref[1] if len(ref) > 1 else ""
    uri = ref[0]
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}" if uri else ""


def route_preview(payload):
    thread = _thread(payload)
    args = Args(_targets_wanted(payload), no_attribution=bool(payload.get("no_attribution")))
    out = {"author": thread.author, "tweets": len(thread.tweets),
           "source_url": thread.source_url, "warnings": list(thread.warnings),
           "urls": [t.url for t in thread.tweets], "targets": {}}
    index = {t.id: i + 1 for i, t in enumerate(thread.tweets)}
    for kind in args.to:
        units = render(thread, kind, attribution_for(thread, args), credit_for(thread, args))
        # A tweet longer than the target's cap becomes several posts; say so, so "1 tweet ->
        # 2 posts" reads as splitting rather than as the reader having grabbed a stray tweet.
        parts = {}
        for u in units:
            parts[u["tweet"].id] = parts.get(u["tweet"].id, 0) + 1
        seen = {}
        rows = []
        for u in units:
            tid = u["tweet"].id
            seen[tid] = seen.get(tid, 0) + 1
            rows.append({"text": u["text"], "images": [m.url for m in u["images"]],
                         "tweet": index.get(tid, 0), "part": seen[tid], "parts": parts[tid]})
        out["targets"][kind] = rows
    return out


def route_publish(payload):
    """Publish, keeping the handler's stdout clean - it is the socket, not a terminal."""
    thread = _thread(payload)
    args = Args(_targets_wanted(payload), all_public=bool(payload.get("all_public")),
                no_attribution=bool(payload.get("no_attribution")))
    targets = build_targets(args)
    if not targets:
        return {"error": f"no target configured - see {config.PATH}"}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as log:
        with redirect_stdout(log):
            failures = do_publish(thread, args, targets)
    from .ledger import Ledger
    result, handle = {}, config.get("ATPROTO_HANDLE", "")
    for tgt in targets:
        count, refs = Ledger().done(thread.root_id, tgt.name)
        result[tgt.name] = {"posts": count, "failed": tgt.name in failures,
                            "url": _web_url(tgt.name, refs[0], handle) if refs else ""}
    notify(thread, result)
    return {"author": thread.author, "results": result, "log": str(LOG)}


def notify(thread, result):
    """A desktop notification, because the browser tab may already be gone."""
    ok = [f"{k} {v['posts']}" for k, v in result.items() if not v["failed"]]
    bad = [k for k, v in result.items() if v["failed"]]
    msg = ("failed: " + ", ".join(bad)) if bad else ("posted " + ", ".join(ok))
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {shlex.quote(msg)} '
                        f'with title "Xthread2social" subtitle "@{thread.author}"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


ROUTES = {"/preview": route_preview, "/publish": route_publish}


def handle(method, path, headers, body):
    """-> (status, payload). Auth first: an unauthenticated caller learns nothing else."""
    token = config.get("LISTENER_TOKEN")
    if not token:
        return 503, {"error": "listener has no token; run xthread2social --install-listener"}
    if headers.get("x-token") != token:
        return 403, {"error": "bad or missing X-Token"}
    if path == "/ping":
        return 200, {"ok": True, "version": 1}
    if method != "POST" or path not in ROUTES:
        return 404, {"error": f"no route for {method} {path}"}
    try:
        payload = json.loads(body or b"{}")
    except ValueError as e:
        return 400, {"error": f"bad json: {e}"}
    try:
        return 200, ROUTES[path](payload)
    except IncompleteError as e:
        # Not an error the user can fix by editing a URL: the replies may simply be other
        # people's. Hand it back as a question so the overlay can offer "publish anyway".
        return 200, {"needs_confirm": str(e)}
    except ReadError as e:
        return 400, {"error": str(e)}
    except Exception as e:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as log:
            traceback.print_exc(file=log)
        return 500, {"error": f"{type(e).__name__}: {e}", "log": str(LOG)}


def read_request(fin):
    """Parse one HTTP request off a socket we were handed. No framework, no listener."""
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = fin.read(1)
        if not chunk:
            return None, None, {}, b""
        head += chunk
        if len(head) > MAX_BODY:
            return None, None, {}, b""
    raw, _, rest = head.partition(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    method, path = (parts + ["", ""])[:2]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = min(int(headers.get("content-length") or 0), MAX_BODY)
    body = rest + (fin.read(n - len(rest)) if n > len(rest) else b"")
    return method, path.split("?")[0], headers, body


def main(argv=None):
    config.load()
    config.resolve_secrets(config.SECRETS + ("LISTENER_TOKEN",))
    fin, fout = sys.stdin.buffer, sys.stdout.buffer
    method, path, headers, body = read_request(fin)
    if not method:
        return 0
    status, payload = handle(method, path, headers, body)
    blob = json.dumps(payload).encode()
    fout.write(f"HTTP/1.1 {status} {'OK' if status == 200 else 'ERROR'}\r\n"
               f"Content-Type: application/json\r\n"
               f"Content-Length: {len(blob)}\r\n"
               f"Connection: close\r\n\r\n".encode() + blob)
    fout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
