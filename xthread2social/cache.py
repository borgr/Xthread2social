"""Short-lived cache of read threads, keyed by the tweet ids asked for.

Two things need it. The overlay re-renders the preview every time you edit the note box, and
publishing re-reads the thread it just previewed - both would otherwise re-walk the whole
chain against X's endpoint, one request per tweet with a pause between them. It also means a
thread previewed a minute ago still publishes if the endpoint goes down in between.

Deliberately a file, not a process cache: launchd starts a fresh handler per connection, so
there is no memory to keep anything in.
"""
import hashlib
import json
import time
from pathlib import Path

from .ledger import DEFAULT as LEDGER_PATH
from .model import Thread

DIR = LEDGER_PATH.parent / "cache"
TTL = 1800                                       # 30 min: long enough to write a note


def _key(ids, allow_incomplete):
    raw = ",".join(ids) + f"|{bool(allow_incomplete)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _sweep():
    now = time.time()
    for f in DIR.glob("*.json"):
        if now - f.stat().st_mtime > TTL:
            f.unlink(missing_ok=True)


def get(ids, allow_incomplete):
    f = DIR / f"{_key(ids, allow_incomplete)}.json"
    if not f.exists() or time.time() - f.stat().st_mtime > TTL:
        return None
    try:
        return Thread.from_json(f.read_text())
    except (ValueError, KeyError, TypeError):
        return None                              # a corrupt entry is just a cache miss


def put(ids, allow_incomplete, thread):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        _sweep()
        (DIR / f"{_key(ids, allow_incomplete)}.json").write_text(thread.to_json())
    except OSError:
        pass                                     # a cache that cannot write is still a cache
    return thread
