"""Fetch and prepare images for upload."""
import io
import urllib.request

from .textutil import MAX_IMAGES

UA = {"User-Agent": "Mozilla/5.0 (compatible; xthread2social/0.1)"}
BLUESKY_BLOB_CAP = 976_000          # under Bluesky's ~1MB blob limit, with headroom


def download(url, cap=20_000_000, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(cap)


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
