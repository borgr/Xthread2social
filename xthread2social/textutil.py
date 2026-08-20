"""Text splitting for Bluesky and Mastodon.

VENDORED from discord_atproto_bridge/relay.py @ 8c31c09 (functions `display_for` ..
`build_richtext`, unmodified). Vendored rather than imported so this tool never loads a
module that can reach the colab-links credentials; `tests/test_vendor_drift.py` fails if
upstream changes. Local additions are below the marker.
"""
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>()]+[^\s<>().,!?;:'\"]")
LIMIT = 290          # margin under Bluesky's 300-GRAPHEME cap
MASTODON_LIMIT = 480 # margin under Mastodon's 500-char cap
MASTODON_URL_LEN = 23
MAX_IMAGES = 4


def display_for(url):
    """Shortened display text for long URLs (full URL is still the link target)."""
    if len(url) <= 60:
        return url
    netloc = urlparse(url).netloc
    return f"{netloc}/…"


# Tokens ending in "." that are NOT sentence ends (kept lowercase, dot-stripped).
ABBREV = {"e.g", "i.e", "eg", "ie", "al", "et", "fig", "vs", "dr", "mr", "mrs",
          "ms", "prof", "st", "cf", "etc", "no", "vol", "eq", "sec", "ref",
          "pp", "approx", "resp", "inc", "ltd", "figs", "eqs"}


def _default_url_eff(u):
    """Bluesky: a URL costs its shortened *display* length."""
    return len(display_for(u))


def _url_reductions(text, url_eff=None):
    """(start, end, saved) per URL: full length minus its *effective* (counted) length,
    so a very long URL (e.g. a Scholar alert link) doesn't force a needless split.
    `url_eff(url)->int` defaults to Bluesky's shortened display; Mastodon passes a flat 23."""
    if url_eff is None:
        url_eff = _default_url_eff
    return [(m.start(), m.end(), len(m.group(0)) - url_eff(m.group(0)))
            for m in URL_RE.finditer(text)]


def _eff(text, a, b, red):
    """Effective (display) length of text[a:b]. URLs contain no whitespace, so each
    URL is always fully inside one chunk -> subtract its display saving in full."""
    n = b - a
    for s, e, saved in red:
        if a <= s and e <= b:
            n -= saved
    return n


def _is_sentence_end(text, i, red):
    """Whether text[i] ('.', '!' or '?') really ends a sentence (not a decimal,
    arXiv id, URL, or abbreviation like 'e.g.'/'et al.')."""
    if any(s <= i < e for s, e, _ in red):      # inside a URL
        return False
    if text[i] in "!?":
        return True
    if i > 0 and text[i - 1].isdigit():          # 3.14, 2606.24579, v2.
        return False
    j = i                                        # trailing alpha run before the dot
    while j > 0 and text[j - 1].isalpha():
        j -= 1
    word = text[j:i].lower()
    if len(word) <= 1:                           # "e.g.", "U.S.", initials
        return False
    return word not in ABBREV


def _boundaries(text, red):
    """Every whitespace run as (content_end, next_start, priority):
    4 = paragraph (newline), 3 = sentence, 2 = clause (,;:), 1 = plain space."""
    out, i, n = [], 0, len(text)
    while i < n:
        if not text[i].isspace():
            i += 1
            continue
        a = i
        while i < n and text[i].isspace():
            i += 1
        prev = text[a - 1] if a > 0 else ""
        if "\n" in text[a:i]:
            p = 4
        elif prev in ".!?" and _is_sentence_end(text, a - 1, red):
            p = 3
        elif prev in ",;:":
            p = 2
        else:
            p = 1
        out.append((a, i, p))
    return out


def chunk_text(text, reserve=0, limit=None, url_eff=None):
    """Split into <=limit posts, preferring sentence > clause > word breaks and
    balancing sizes so a thread doesn't end in a tiny orphan. `reserve` chars are
    kept free on the LAST post (for the author suffix). `limit`/`url_eff` default to
    Bluesky (290 chars, shortened-URL display); Mastodon passes 480 and a flat-23 url_eff."""
    if limit is None:
        limit = LIMIT
    text = text.strip()
    if not text:
        return [""]
    red = _url_reductions(text, url_eff)
    if _eff(text, 0, len(text), red) <= limit:
        chunks = [text]
    else:
        bounds = _boundaries(text, red)
        chunks, start = [], 0
        while start < len(text):
            if _eff(text, start, len(text), red) <= limit:   # remainder fits
                chunks.append(text[start:].strip())
                break
            remaining = _eff(text, start, len(text), red)
            target = remaining / (-(-remaining // limit))     # balanced size (ceil posts)
            feasible = [(a, b, p) for (a, b, p) in bounds
                        if a > start < len(text)
                        and 0 < _eff(text, start, a, red) <= limit]
            if not feasible:                                  # unbreakable token > limit
                chunks.append(text[start:start + limit])
                start += limit
                continue
            accept = [x for x in feasible
                      if _eff(text, start, x[0], red) >= target * 0.6]  # not too short
            a, b, _ = min(accept or feasible,
                          key=lambda x: (-x[2], abs(_eff(text, start, x[0], red) - target)))
            chunks.append(text[start:a].strip())
            start = b
    if not chunks:
        chunks = [""]
    # keep room for the author suffix on the last post
    last = chunks[-1]
    if reserve and last and _eff(last, 0, len(last), _url_reductions(last, url_eff)) + reserve > limit:
        chunks.append("")
    return chunks


def build_richtext(chunk, client_utils):
    """Build a TextBuilder so URLs in the chunk are clickable."""
    tb = client_utils.TextBuilder()
    pos = 0
    for m in URL_RE.finditer(chunk):
        if m.start() > pos:
            tb.text(chunk[pos:m.start()])
        url = m.group(0)
        tb.link(display_for(url), url)
        pos = m.end()
    if pos < len(chunk):
        tb.text(chunk[pos:])
    return tb


# ---------- local additions ----------

def mastodon_eff(u):
    """Mastodon counts every URL as exactly 23 characters."""
    return MASTODON_URL_LEN


def glen(text):
    """Grapheme count when `regex` is installed, else codepoints.

    Bluesky enforces 300 *graphemes*; a family emoji is one grapheme but many
    codepoints, so a plain len() over-counts and splits a post that would have fit.
    Optional dependency: without it the wider LIMIT margin absorbs the difference.
    """
    try:
        import regex
    except ImportError:
        return len(text)
    return len(regex.findall(r"\X", text))


def chunk_for_bluesky(text, reserve=0, limit=None):
    """chunk_text, then guarantee every chunk fits Bluesky's 300-grapheme cap.

    The vendored splitter counts codepoints; shrink the limit and re-split until the
    grapheme count fits, so an emoji-dense post can't be rejected mid-thread (which
    would leave earlier posts already live).
    """
    if limit is None:
        limit = LIMIT
    for _ in range(6):
        chunks = chunk_text(text, reserve=reserve, limit=limit)
        if all(glen(c) + (reserve if c is chunks[-1] else 0) <= 300 for c in chunks):
            return chunks
        limit -= 20
    return [c[:280] for c in chunks]
