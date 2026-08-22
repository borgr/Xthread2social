"""`xthread2social --doctor`: one command that says which layer is broken.

There are five things that can independently stop a publish - credentials, the secret-store
token, the socket-activated agent, X's unofficial syndication endpoint, and a stale copy of the
userscript in the browser - and from inside Chrome they all look identical ("listener not
reachable"). This walks them in order and prints a line per layer, so the answer never
depends on remembering which of them to suspect first.

Read-only: it authenticates and fetches, but writes nothing and posts nothing.
"""
import json
import re
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import __version__, config
from .ledger import DEFAULT as LEDGER_PATH

CANARY = "20"                    # @jack's first tweet: public, ancient, unlikely to vanish
RAW_URL = ("https://raw.githubusercontent.com/borgr/Xthread2social/main/"
           "userscript/xthread2social.user.js")
LOCAL_USERSCRIPT = Path(__file__).resolve().parent.parent / "userscript/xthread2social.user.js"
VERSION_RE = re.compile(r"@version\s+([0-9.]+)")


def _line(state, what, detail=""):
    print(f"[{state:^4}] {what}" + (f": {detail}" if detail else ""))
    return state != "FAIL"


def check_install():
    _line("ok", "package", f"xthread2social {__version__} ({sys.executable})")
    ok = _line("ok" if config.PATH.exists() else "FAIL", "config", str(config.PATH))
    for name in config.SECRETS + ("LISTENER_TOKEN",):
        got = bool(config.keychain(name))
        ok &= _line("ok" if got else "FAIL", f"keychain {name}",
                    "present" if got else "missing - see README")
    return ok


def check_listener():
    """Agent installed, loaded, pointing at a binary that exists, and answering."""
    from . import listener
    try:
        mgr = listener.manager()
    except listener.Unsupported as e:
        return _line("FAIL", "listener", str(e))
    ok = True
    for f in listener.unit_files():
        ok &= _line("ok" if f.exists() else "FAIL", f"{mgr} unit", str(f))
    try:
        _line("ok", "serve binary", listener.serve_binary())
    except RuntimeError as e:
        ok &= _line("FAIL", "serve binary", str(e))
    loaded, _ = listener.status()
    # On Windows "loaded" already includes "and answering": a resident server can be
    # registered at logon and dead right now, which socket activation cannot be.
    ok &= _line("ok" if loaded else "FAIL", "agent loaded",
                listener.status_command() if loaded
                else f"not active ({listener.status_command()}) - run --install-listener")
    token = config.get("LISTENER_TOKEN") or config.keychain("LISTENER_TOKEN")
    if not token:
        return _line("FAIL", "listener /ping", "no token to call it with") and ok
    try:
        with socket.create_connection(("127.0.0.1", listener.PORT), timeout=10) as s:
            s.sendall(f"GET /ping HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Token: {token}\r\n"
                      f"Connection: close\r\n\r\n".encode())
            raw = b""
            while chunk := s.recv(4096):
                raw += chunk
        body = raw.partition(b"\r\n\r\n")[2].decode("utf-8", "ignore")
        got = json.loads(body or "{}")
        ok &= _line("ok" if got.get("ok") else "FAIL", "listener /ping",
                    f"answered {got}")
    except Exception as e:                        # noqa: BLE001 - any failure is the report
        ok &= _line("FAIL", "listener /ping", f"{type(e).__name__}: {e}")
    return ok


def check_reader():
    """The syndication endpoint is unofficial: if X changes it, this is what breaks first."""
    from .read_syndication import ReadError, fetch, to_tweet
    try:
        raw = fetch(CANARY, timeout=15, retries=1)
        t = to_tweet(raw)
    except (ReadError, Exception) as e:           # noqa: BLE001
        return _line("FAIL", "syndication endpoint", f"{type(e).__name__}: {e}")
    missing = [k for k in ("id_str", "text", "user") if k not in raw]
    if missing or not t.author:
        return _line("FAIL", "syndication endpoint",
                     f"reachable but the payload changed shape (missing {missing or 'user'})")
    return _line("ok", "syndication endpoint", f"tweet {CANARY} reads as @{t.author}")


def check_userscript():
    """A browser running an old copy is the most common 'it behaves wrong' report."""
    local = ""
    if LOCAL_USERSCRIPT.exists():
        m = VERSION_RE.search(LOCAL_USERSCRIPT.read_text())
        local = m.group(1) if m else ""
        _line("ok" if local else "FAIL", "userscript (repo)", local or "no @version found")
    try:
        req = urllib.request.Request(RAW_URL, headers={"User-Agent": "xthread2social"})
        with urllib.request.urlopen(req, timeout=15) as r:
            m = VERSION_RE.search(r.read().decode("utf-8", "ignore"))
        served = m.group(1) if m else ""
    except Exception as e:                        # noqa: BLE001
        return _line("warn", "userscript (github)", f"could not check: {e}")
    same = (not local) or served == local
    return _line("ok" if same else "warn", "userscript (github)",
                 f"serving {served}" + ("" if same else f" but the repo has {local} - push it"))


def check_state():
    entries = {}
    if LEDGER_PATH.exists():
        entries = json.loads(LEDGER_PATH.read_text() or "{}")
    _line("ok", "ledger", f"{len(entries)} thread/target pair(s) in {LEDGER_PATH}")
    log = LEDGER_PATH.parent / "serve.log"
    if log.exists():
        size = log.stat().st_size
        _line("ok", "listener log", f"{size // 1024} KiB at {log}")
        for line in log.read_text(errors="ignore").splitlines()[-5:]:
            print(f"         | {line}")
    else:
        _line("warn", "listener log", "nothing published through the browser yet")
    return True


def check_credentials_quietly(check_credentials, args):
    print()
    return check_credentials(args) == 0


def run(check_credentials, args):
    """Print the report. Returns a shell exit code: 0 all good, 1 something is broken."""
    ok = check_install()
    print()
    ok &= check_listener()
    print()
    ok &= check_reader()
    ok &= check_userscript()
    print()
    ok &= check_state()
    ok &= check_credentials_quietly(check_credentials, args)
    print("\n" + ("everything answers" if ok else
                  "something above says FAIL - fix the topmost one first"))
    return 0 if ok else 1
