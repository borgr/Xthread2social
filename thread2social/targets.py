"""Writers. Each target prepares everything that can fail *before* publishing anything.

Bluesky blobs and Mastodon media ids are invisible until a record references them, so
uploading all media up front turns a download/resize/upload failure into a no-op instead
of a half-published thread.
"""
import time

from .media import fetch_photo, photos_of, shrink
from .textutil import (MASTODON_LIMIT, build_richtext, chunk_for_bluesky, chunk_text,
                       glen, mastodon_eff)


class PostError(Exception):
    """A target failed. Anything already published stays in the ledger."""


def render(thread, limit_kind="bluesky", attribution=""):
    """Thread -> list of {text, tweet} units, one per post, in order.

    A tweet longer than the target's cap becomes several consecutive posts; images ride
    on the first post of their tweet, matching where they appeared on X.
    """
    units = []
    for i, t in enumerate(thread.tweets):
        text = t.text
        if t.quoted:
            text = f"{text}\n\n> {t.quoted}"
        last = i == len(thread.tweets) - 1
        reserve = len(attribution) if (last and attribution) else 0
        if limit_kind == "bluesky":
            chunks = chunk_for_bluesky(text, reserve=reserve)
        else:
            chunks = chunk_text(text, reserve=reserve, limit=MASTODON_LIMIT,
                                url_eff=mastodon_eff)
        if last and attribution:
            chunks[-1] = (chunks[-1] + attribution).strip()
        for j, c in enumerate(chunks):
            units.append({"text": c, "tweet": t, "images": photos_of(t) if j == 0 else []})
    return units


class Bluesky:
    name = "bluesky"

    def __init__(self, handle, app_password, pds="https://bsky.social"):
        self.handle, self.app_password, self.pds = handle, app_password, pds
        self.client = None

    def login(self):
        """Log in, retrying only *transient* failures.

        createSession is rate-limited to a handful of attempts per day, and a rejected
        credential is not transient - retrying a 401 three times burns a third of the
        daily quota to learn the same thing three times.
        """
        from atproto import Client
        from atproto_client.exceptions import UnauthorizedError
        self.client = Client(base_url=self.pds)
        last = None
        for attempt in range(3):
            try:
                self.client.login(self.handle, self.app_password)
                return
            except UnauthorizedError as e:
                raise PostError(
                    f"Bluesky rejected the credentials for {self.handle}: "
                    f"{getattr(e.response, 'content', e)}") from e
            except Exception as e:                       # network/5xx blips are common
                last = e
                time.sleep(2 * (attempt + 1))
        raise PostError(f"Bluesky login failed after 3 tries: {type(last).__name__}: {last}")

    def prepare(self, units):
        """Phase 1: upload every image as a blob. Nothing is visible yet."""
        from atproto import models
        for u in units:
            embeds = []
            for m in u["images"]:
                data, _ = shrink(fetch_photo(m.url))
                blob = self.client.upload_blob(data).blob
                embeds.append(models.AppBskyEmbedImages.Image(alt=m.alt or "", image=blob))
            u["embed"] = models.AppBskyEmbedImages.Main(images=embeds) if embeds else None
        return units

    def publish(self, units, start=0, refs=None):
        """Phase 2: create records, threading each onto the previous."""
        from atproto import client_utils, models
        root_ref = parent_ref = None
        if refs:
            root_ref = models.create_strong_ref(_Ref(*refs[0]))
            parent_ref = models.create_strong_ref(_Ref(*refs[-1]))
        out = list(refs or [])
        for i, u in enumerate(units):
            if i < start:
                continue
            reply = (models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
                     if parent_ref is not None else None)
            tb = build_richtext(u["text"], client_utils)
            resp = self.client.send_post(tb, reply_to=reply, embed=u.get("embed"))
            ref = models.create_strong_ref(resp)
            root_ref = root_ref or ref
            parent_ref = ref
            out.append((resp.uri, resp.cid))
            yield i, out
            time.sleep(1)


class _Ref:
    """Minimal (uri, cid) holder so a resumed run can rebuild a strong ref."""
    def __init__(self, uri, cid):
        self.uri, self.cid = uri, cid


class Mastodon:
    name = "mastodon"

    def __init__(self, base, token, all_public=False):
        self.base, self.token, self.all_public = base.rstrip("/"), token, all_public

    def _req(self, path, method="GET", body=None, raw=None, content_type=None,
             timeout=45, retries=2):
        import json as _json
        import urllib.error
        import urllib.request
        headers = {"Authorization": f"Bearer {self.token}",
                   "User-Agent": "thread2social/0.1"}
        data = None
        if raw is not None:
            data = raw
            if content_type:
                headers["Content-Type"] = content_type
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = _json.dumps(body).encode()
        last = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    payload = r.read().decode("utf-8", "ignore")
                    return _json.loads(payload) if payload else {}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise PostError(f"Mastodon token rejected (HTTP {e.code}) - expired, "
                                    f"or missing write:statuses/write:media scope")
                if e.code < 500:
                    raise PostError(f"Mastodon HTTP {e.code} {e.read()[:200].decode('utf-8','ignore')}")
                last = e
            except Exception as e:
                last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
        raise PostError(f"Mastodon unreachable: {last}")

    def login(self):
        acct = self._req("/api/v1/accounts/verify_credentials", retries=1, timeout=20)
        self.username = acct.get("username", "?")

    def _multipart(self, filename, mime, data, fields):
        import uuid
        b = uuid.uuid4().hex
        nl = b"\r\n"
        buf = []
        for k, v in fields.items():
            buf += [b"--", b.encode(), nl,
                    f'Content-Disposition: form-data; name="{k}"'.encode(), nl, nl,
                    str(v).encode(), nl]
        buf += [b"--", b.encode(), nl,
                f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode(),
                nl, f"Content-Type: {mime}".encode(), nl, nl, data, nl,
                b"--", b.encode(), b"--", nl]
        return b"".join(buf), f"multipart/form-data; boundary={b}"

    def prepare(self, units):
        for u in units:
            ids = []
            for m in u["images"]:
                data = fetch_photo(m.url)
                raw, ctype = self._multipart(m.filename, m.mime, data,
                                             {"description": m.alt or ""})
                res = self._req("/api/v2/media", "POST", raw=raw, content_type=ctype,
                                timeout=120)
                mid = res.get("id")
                if mid and not res.get("url"):          # 202 accepted, still processing
                    for _ in range(8):
                        time.sleep(2)
                        if self._req(f"/api/v1/media/{mid}", timeout=30, retries=1).get("url"):
                            break
                if not mid:
                    raise PostError(f"Mastodon media upload returned no id for {m.url}")
                ids.append(mid)
            u["media_ids"] = ids
        return units

    def publish(self, units, start=0, refs=None):
        reply_to = refs[-1][0] if refs else None
        out = list(refs or [])
        for i, u in enumerate(units):
            if i < start:
                continue
            body = {"status": u["text"],
                    "visibility": "public" if (i == 0 or self.all_public) else "unlisted"}
            if reply_to:
                body["in_reply_to_id"] = reply_to
            if u.get("media_ids"):
                body["media_ids"] = u["media_ids"]
            res = self._req("/api/v1/statuses", "POST", body=body)
            reply_to = res.get("id")
            out.append((reply_to, res.get("url", "")))
            yield i, out
            time.sleep(1)
