"""Progress ledger: which posts of which thread reached which target.

Keyed (root_id, target) and storing per-post refs, so a failure at post 5 of 9 resumes
at 5 with the correct parent instead of reposting the first four or refusing to retry.
"""
import json
import os
from pathlib import Path

DEFAULT = Path(os.environ.get("THREAD2SOCIAL_HOME",
                              Path.home() / ".local/share/thread2social")) / "ledger.json"


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
