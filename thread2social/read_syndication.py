"""Reader A: build a Thread from X's public syndication endpoint.

No auth, no browser, no key. Each tweet's JSON carries `in_reply_to_status_id_str`,
so a thread is walked *backwards* from its last tweet. Verified 2026-08-19 against
an 8-tweet thread with 9 photos and expanded t.co links.
"""
import html
import json
import re
import time
import urllib.error
import urllib.request

from .model import Media, Thread, Tweet

ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a"
UA = {"User-Agent": "Mozilla/5.0 (compatible; thread2social/0.1)"}
STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)")
MAX_HOPS = 100


class ReadError(Exception):
    """The thread could not be read or failed a completeness gate."""


def parse_ids(urls):
    """Tweet ids from any mix of URLs or bare ids, deduped and numerically sorted.
    Order-insensitive by design: snowflake ids are time-ordered, so the caller can
    paste the two ends of a thread in either order."""
    ids = []
    for u in urls:
        u = u.strip()
        m = STATUS_RE.search(u)
        if m:
            ids.append(m.group(1))
        elif u.isdigit():
            ids.append(u)
        else:
            raise ReadError(f"not a tweet URL or id: {u!r}")
    return sorted(set(ids), key=int)


def fetch(tweet_id, timeout=20, retries=2):
    url = ENDPOINT.format(id=tweet_id)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ReadError(f"tweet {tweet_id} is unavailable (HTTP {e.code}) - "
                                f"deleted, or a protected account the endpoint won't serve")
            last = e
        except Exception as e:
            last = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise ReadError(f"could not fetch tweet {tweet_id}: {last}")


def _text_of(d):
    """Visible text: `note_tweet` for long posts, otherwise `text`.

    X appends a t.co link to the text for each attached photo; those are removed by
    matching `mediaDetails[].url` exactly, rather than by slicing `display_text_range`
    (whose indices are UTF-16 units over partly-unescaped text - two off-by-N traps).
    Remaining t.co links are expanded, then HTML entities are unescaped.
    """
    note = (d.get("note_tweet") or {}).get("text")
    if note:
        text = note
        ents = ((d.get("note_tweet") or {}).get("entity_set") or d.get("entities") or {})
    else:
        text = d.get("text", "")
        ents = d.get("entities") or {}
    for m in d.get("mediaDetails") or []:
        if m.get("url"):
            text = text.replace(m["url"], "")
    for u in ents.get("urls") or []:
        if u.get("url") and u.get("expanded_url"):
            text = text.replace(u["url"], u["expanded_url"])
    text = html.unescape(text).strip()
    return text, [u["expanded_url"] for u in (ents.get("urls") or [])
                  if u.get("expanded_url")]


def _media_of(d):
    out = []
    for m in d.get("mediaDetails") or []:
        url = m.get("media_url_https") or ""
        if not url:
            continue
        mime = "image/png" if url.endswith(".png") else "image/jpeg"
        out.append(Media(url=url, kind=m.get("type", "photo"),
                         alt=m.get("ext_alt_text") or "", mime=mime))
    return out


def _quoted_of(d):
    q = d.get("quoted_tweet")
    if not q:
        return ""
    qtext, _ = _text_of(q)
    return f"@{(q.get('user') or {}).get('screen_name', '?')}: {qtext}"


def to_tweet(d):
    text, links = _text_of(d)
    return Tweet(
        id=d["id_str"],
        text=text,
        author=(d.get("user") or {}).get("screen_name", ""),
        media=_media_of(d),
        links=links,
        quoted=_quoted_of(d),
        reply_to=d.get("in_reply_to_status_id_str") or "",
        reply_count=int(d.get("conversation_count") or 0),
    )


def walk(tail, declared_root="", fetcher=fetch, pause=0.4):
    """Walk backwards from `tail`, stopping at the declared root, the conversation
    root, or the first tweet by a different author. Returns oldest-first."""
    chain, seen, author, cur = [], set(), None, tail
    for _ in range(MAX_HOPS):
        t = to_tweet(fetcher(cur))
        if author is None:
            author = t.author
        elif t.author != author:
            break
        chain.append(t)
        seen.add(t.id)
        if cur == declared_root or not t.reply_to or t.reply_to in seen:
            break
        cur = t.reply_to
        if pause:
            time.sleep(pause)
    else:
        raise ReadError(f"thread longer than {MAX_HOPS} tweets - refusing to continue")
    chain.reverse()
    return chain


def check(chain, declared_root="", allow_incomplete=False):
    """The completeness gates, all run before anything is posted. Returns warnings."""
    if not chain:
        raise ReadError("no tweets read")
    if declared_root and chain[0].id != declared_root:
        raise ReadError(
            f"incomplete: walked back to {chain[0].id} but you declared {declared_root} as the "
            f"first tweet. Pass only the last tweet to walk as far as the chain goes.")
    for a, b in zip(chain, chain[1:]):
        if b.reply_to != a.id:
            raise ReadError(f"gap in the chain: {b.id} replies to {b.reply_to}, not {a.id}")

    warnings = []
    # Nothing proves the tail is the end, but replies to it mean there may be more below.
    if not declared_root and chain[-1].reply_count:
        msg = (f"the last tweet ({chain[-1].id}) has {chain[-1].reply_count} repl(y/ies) - if one "
               f"is the author's own continuation the thread is longer than this. Pass the real "
               f"last tweet's URL, or re-run with --allow-incomplete.")
        if not allow_incomplete:
            raise ReadError(msg)
        warnings.append(msg)
    av = [t.id for t in chain if any(m.kind != "photo" for m in t.media)]
    if av:
        warnings.append(f"video/GIF is not reposted in v1 - the source link is kept instead "
                        f"({len(av)} tweet(s): {', '.join(av)})")
    noalt = sum(1 for t in chain for m in t.media if m.kind == "photo" and not m.alt)
    if noalt:
        warnings.append(f"{noalt} image(s) have no alt text - the syndication endpoint does not "
                        f"expose it; add alt with --alt or accept it")
    return warnings


def read_thread(urls, allow_incomplete=False, fetcher=fetch, pause=0.4):
    ids = parse_ids(urls)
    if not ids:
        raise ReadError("no tweet URL given")
    tail, declared_root = ids[-1], (ids[0] if len(ids) > 1 else "")
    chain = walk(tail, declared_root, fetcher=fetcher, pause=pause)
    warnings = check(chain, declared_root, allow_incomplete)
    return Thread(author=chain[0].author, tweets=chain,
                  source_url=chain[0].url, warnings=warnings)
