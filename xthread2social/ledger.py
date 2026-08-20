"""Progress ledger: which posts of which thread reached which target.

Keyed (root_id, target) and storing per-post refs, so a failure at post 5 of 9 resumes
at 5 with the correct parent instead of reposting the first four or refusing to retry.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path

DEFAULT = Path(os.environ.get("XTHREAD2SOCIAL_HOME",
                              Path.home() / ".local/share/xthread2social")) / "ledger.json"


class Ledger:
    def __init__(self, path=DEFAULT):
        self.path = Path(path)
        self.data = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text() or "{}")

    @staticmethod
    def _key(root_id, target):
        return f"{root_id}:{target}"

    def done(self, root_id, target):
        """(count_published, refs) for a thread/target pair."""
        e = self.data.get(self._key(root_id, target))
        if not e:
            return 0, []
        return e["count"], [tuple(r) for r in e["refs"]]

    def record(self, root_id, target, count, refs, source_url=""):
        self.data[self._key(root_id, target)] = {
            "count": count, "refs": [list(r) for r in refs], "source_url": source_url}
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)                    # atomic: no half-written ledger


class Busy(Exception):
    """Another process is already publishing this thread."""


@contextmanager
def lock(root_id, path=None):
    """Hold an exclusive per-thread lock for the length of a publish.

    launchd starts a fresh handler per connection, so two overlays - two tabs, or a retry
    after the browser's request timed out while the first publish was still uploading - are
    two processes reading the same ledger at once. Both would see "0 posted" and post the
    whole thread twice, which is exactly the failure the ledger exists to prevent. The lock
    is per thread rather than global so publishing two different threads at once still works.
    """
    import fcntl
    d = Path(path or DEFAULT).parent
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"publish-{root_id}.lock", "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"thread {root_id} is already being published by another window; "
                       f"wait for it to finish, then re-run to resume if it stopped early")
        yield
    finally:
        f.close()                                # closing releases the flock
