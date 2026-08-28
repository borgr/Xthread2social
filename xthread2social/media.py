"""Fetch and prepare images and video for upload."""
import io
import urllib.request

from .textutil import MAX_IMAGES

UA = {"User-Agent": "Mozilla/5.0 (compatible; xthread2social/0.1)"}
BLUESKY_BLOB_CAP = 976_000          # under Bluesky's ~1MB blob limit, with headroom
# app.bsky.embed.video accepts up to 300 MB, Mastodon instances commonly cap video at 40-99 MB,
# so the smaller of the two is what a variant has to fit for both targets to take it.
VIDEO_CAP = 40_000_000


def download(url, cap=20_000_000, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(cap)


def fetch_sized(url, cap):
    """Bytes for a URL, refusing anything over `cap` instead of truncating it.

    A part-downloaded MP4 uploads without complaint and then will not play, so the length is
    checked against Content-Length and against the cap before the bytes are handed on.
    """
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        declared = int(r.headers.get("Content-Length") or 0)
        if declared > cap:
            raise ValueError(f"{declared/1e6:.1f} MB exceeds the {cap/1e6:.0f} MB cap")
        data = r.read()
    if declared and len(data) != declared:
        raise ValueError(f"got {len(data)} of {declared} bytes")
    if len(data) > cap:
        raise ValueError(f"{len(data)/1e6:.1f} MB exceeds the {cap/1e6:.0f} MB cap")
    return data


def fetch_video(media, cap=VIDEO_CAP):
    return fetch_sized(media.url, cap)


def best_variant(video_info, cap=VIDEO_CAP):
    """The highest-bitrate MP4 variant whose estimated size fits `cap`, or None.

    X reports peak bitrate, so the estimate runs high and the choice is conservative. The
    lowest variant is returned when even it looks too big, since the size guard in
    fetch_sized() is what actually decides.
    """
    seconds = (video_info.get("duration_millis") or 0) / 1000 or 1
    mp4 = [v for v in video_info.get("variants") or []
           if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4:
        return None
    mp4.sort(key=lambda v: v.get("bitrate") or 0)
    fits = [v for v in mp4 if (v.get("bitrate") or 0) / 8 * seconds <= cap]
    return (fits or mp4)[-1] if fits else mp4[0]


def fetch_photo(url):
    """Full-resolution bytes for an X photo (`?name=orig` beats the default crop)."""
    sep = "&" if "?" in url else "?"
    try:
        return download(f"{url}{sep}name=orig")
    except Exception:
        return download(url)


def shrink(data, cap=BLUESKY_BLOB_CAP):
    """Re-encode until the image fits `cap` bytes. Returns (bytes, mime)."""
    if len(data) <= cap:
        return data, ("image/png" if data[:4] == b"\x89PNG" else "image/jpeg")
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    for scale, quality in ((1.0, 85), (1.0, 70), (0.8, 75), (0.6, 70), (0.45, 65), (0.3, 60)):
        buf = io.BytesIO()
        work = im if scale == 1.0 else im.resize(
            (max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        work.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= cap:
            return buf.getvalue(), "image/jpeg"
    return buf.getvalue(), "image/jpeg"


def photos_of(tweet, limit=MAX_IMAGES):
    return [m for m in tweet.media if m.kind == "photo"][:limit]


def video_of(tweet):
    """The one video or animated GIF on a tweet, or None.

    Both targets take a single video per post and never alongside images, which is also all X
    allows on one tweet.
    """
    for m in tweet.media:
        if m.is_video and m.url:
            return m
    return None
