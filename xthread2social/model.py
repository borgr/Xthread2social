"""The thread contract: readers produce a Thread, writers consume one.

Keeping this dataclass as the only interface is what lets the reader be swapped
(syndication endpoint today, userscript tomorrow) without touching the writers.
"""
from dataclasses import dataclass, field, asdict
import json


@dataclass
class Media:
    url: str
    kind: str = "photo"            # photo | video | animated_gif
    alt: str = ""
    mime: str = "image/jpeg"

    @property
    def filename(self):
        return self.url.rsplit("/", 1)[-1].split("?")[0] or "image.jpg"


@dataclass
class Tweet:
    id: str
    text: str
    author: str
    media: list = field(default_factory=list)
    links: list = field(default_factory=list)
    quoted: str = ""               # "@handle: text" of a quoted tweet, if any
    reply_to: str = ""
    reply_count: int = 0

    @property
    def url(self):
        return f"https://x.com/{self.author}/status/{self.id}"


@dataclass
class Thread:
    author: str
    tweets: list
    source_url: str = ""
    warnings: list = field(default_factory=list)

    @property
    def root_id(self):
        return self.tweets[0].id if self.tweets else ""

    def to_json(self, indent=1):
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, blob):
        d = json.loads(blob) if isinstance(blob, str) else blob
        tweets = [Tweet(**{**t, "media": [Media(**m) for m in t.get("media", [])]})
                  for t in d["tweets"]]
        return cls(author=d["author"], tweets=tweets,
                   source_url=d.get("source_url", ""), warnings=d.get("warnings", []))
