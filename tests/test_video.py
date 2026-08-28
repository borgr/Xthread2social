"""Video and animated-GIF reposting: variant choice, the size guard, and both writers.

`fixtures/video_raw.json` is a captured syndication payload for a real 9-second video tweet.
The animated-GIF payload below is hand-written, since X serves GIFs in the same shape with
no `duration_millis`.
"""
import json
import pathlib
import unittest
from unittest import mock

from xthread2social import media as media_mod
from xthread2social.media import VIDEO_CAP, best_variant, fetch_sized, video_of
from xthread2social.model import Media, Thread, Tweet
from xthread2social.read_syndication import check, to_tweet
from xthread2social.targets import Bluesky, Mastodon, PostError, render

RAW = json.loads((pathlib.Path(__file__).parent / "fixtures/video_raw.json").read_text())
VIDEO_TWEET = "2093383151422943511"
MP4 = "https://video.twimg.com/amplify_video/2093157471133278208/vid/avc1/1842x1124/ja0_3fIdy3w7DmxQ.mp4"

GIF_RAW = {
    "id_str": "1", "text": "look", "user": {"screen_name": "someone"},
    "mediaDetails": [{
        "type": "animated_gif",
        "media_url_https": "https://pbs.twimg.com/tweet_video_thumb/abc.jpg",
        "video_info": {"aspect_ratio": [498, 280],
                       "variants": [{"bitrate": 0, "content_type": "video/mp4",
                                     "url": "https://video.twimg.com/tweet_video/abc.mp4"}]}},
    ],
}


def a_video_thread(kind="video"):
    m = Media(url=MP4, kind=kind, mime="video/mp4", duration_ms=9000, width=921, height=562)
    t = Tweet(id="1", text="watch this", author="someone", media=[m])
    return Thread(author="someone", tweets=[t], source_url="https://x.com/someone/status/1")


class TestVariantChoice(unittest.TestCase):
    def info(self):
        return RAW[VIDEO_TWEET]["mediaDetails"][0]["video_info"]

    def test_highest_mp4_wins_when_all_of_them_fit(self):
        self.assertEqual(best_variant(self.info())["url"], MP4)

    def test_the_m3u8_variant_is_never_chosen(self):
        for cap in (VIDEO_CAP, 1_000_000, 1):
            self.assertEqual(best_variant(self.info(), cap)["content_type"], "video/mp4")

    def test_a_small_cap_steps_down_the_ladder(self):
        self.assertEqual(best_variant(self.info(), 1_000_000)["bitrate"], 832_000)

    def test_the_smallest_is_offered_when_none_of_them_fit(self):
        self.assertEqual(best_variant(self.info(), 1000)["bitrate"], 256_000)

    def test_no_mp4_variant_at_all(self):
        self.assertIsNone(best_variant({"variants": [{"content_type": "application/x-mpegURL",
                                                      "url": "x.m3u8"}]}))


class TestReader(unittest.TestCase):
    def test_the_media_url_is_the_mp4_not_the_poster_frame(self):
        m = to_tweet(RAW[VIDEO_TWEET]).media[0]
        self.assertEqual((m.kind, m.mime, m.url), ("video", "video/mp4", MP4))
        self.assertEqual((m.duration_ms, m.width, m.height), (9000, 921, 562))
        self.assertTrue(m.is_video)

    def test_the_attached_video_link_is_stripped_from_the_text(self):
        self.assertNotIn("t.co", to_tweet(RAW[VIDEO_TWEET]).text)

    def test_an_animated_gif_keeps_its_kind(self):
        m = to_tweet(GIF_RAW).media[0]
        self.assertEqual((m.kind, m.duration_ms), ("animated_gif", 0))
        self.assertEqual(m.url, "https://video.twimg.com/tweet_video/abc.mp4")

    def test_a_video_with_no_playable_variant_warns_instead_of_failing(self):
        raw = json.loads(json.dumps(GIF_RAW))
        raw["mediaDetails"][0]["video_info"]["variants"] = []
        warnings = check([to_tweet(raw)], declared_root="1")
        self.assertTrue(any("no MP4 variant" in w for w in warnings), warnings)


class TestSizeGuard(unittest.TestCase):
    def fake_urlopen(self, declared, body):
        class R:
            headers = {"Content-Length": str(declared)}

            def read(self, *_):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return mock.patch.object(media_mod.urllib.request, "urlopen", lambda *a, **k: R())

    def test_a_declared_length_over_the_cap_is_refused_before_downloading(self):
        with self.fake_urlopen(50_000_000, b""):
            with self.assertRaises(ValueError):
                fetch_sized("https://x/v.mp4", 40_000_000)

    def test_a_truncated_download_is_refused_rather_than_uploaded(self):
        with self.fake_urlopen(500, b"x" * 200):
            with self.assertRaises(ValueError):
                fetch_sized("https://x/v.mp4", 40_000_000)

    def test_a_complete_download_passes(self):
        with self.fake_urlopen(3, b"abc"):
            self.assertEqual(fetch_sized("https://x/v.mp4", 40_000_000), b"abc")


class TestRender(unittest.TestCase):
    def test_the_video_rides_on_the_first_post_of_its_tweet(self):
        thread = a_video_thread()
        thread.tweets[0].text = "word " * 200          # forces a split
        units = render(thread, "bluesky")
        self.assertGreater(len(units), 1)
        self.assertEqual(units[0]["video"].url, MP4)
        self.assertTrue(all(u["video"] is None for u in units[1:]))

    def test_videos_false_drops_it(self):
        self.assertIsNone(render(a_video_thread(), "bluesky", videos=False)[0]["video"])

    def test_video_of_ignores_a_variantless_media(self):
        thread = a_video_thread()
        thread.tweets[0].media[0].url = ""
        self.assertIsNone(video_of(thread.tweets[0]))


class FakeUpload:
    """What upload_blob returns. The embed model validates the blob, so it must be a real one."""
    def __init__(self, data):
        from atproto_client.models.blob_ref import BlobRef, IpldLink
        self.blob = BlobRef(mime_type="video/mp4", size=len(data), ref=IpldLink(link="bafyfake"))


class TestBlueskyVideo(unittest.TestCase):
    def embed(self, kind):
        tgt = Bluesky("me.bsky.social", "pw")
        tgt.client = mock.Mock()
        tgt.client.upload_blob.side_effect = lambda data: FakeUpload(data)
        with mock.patch("xthread2social.targets.fetch_video", return_value=b"MP4DATA"):
            units = tgt.prepare(render(a_video_thread(kind), "bluesky"))
        return units[0]["embed"]

    def test_a_video_becomes_an_embed_video_with_its_aspect_ratio(self):
        embed = self.embed("video")
        self.assertEqual(embed.py_type, "app.bsky.embed.video")
        self.assertEqual(embed.video.size, len(b"MP4DATA"))
        self.assertEqual((embed.aspect_ratio.width, embed.aspect_ratio.height), (921, 562))
        self.assertIsNone(embed.presentation)

    def test_an_animated_gif_asks_for_gif_presentation(self):
        self.assertEqual(self.embed("animated_gif").presentation, "gif")

    def test_an_upload_failure_names_the_escape_hatch_and_publishes_nothing(self):
        tgt = Bluesky("me.bsky.social", "pw")
        tgt.client = mock.Mock()
        tgt.client.upload_blob.side_effect = RuntimeError("BlobTooLarge")
        with mock.patch("xthread2social.targets.fetch_video", return_value=b"MP4DATA"):
            with self.assertRaises(PostError) as caught:
                tgt.prepare(render(a_video_thread(), "bluesky"))
        self.assertIn("--no-video", str(caught.exception))


class TestMastodonVideo(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.tgt = Mastodon("https://example.social", "tok")
        self.tgt._req = self.fake_req

    def fake_req(self, path, method="GET", body=None, raw=None, content_type=None,
                 timeout=45, retries=2):
        self.calls.append((path, timeout, raw))
        if path == "/api/v2/media":
            return {"id": "77"}                        # 202, still transcoding
        return {"id": "77", "url": "https://example.social/media/77.mp4"}

    def test_the_mp4_is_uploaded_and_polled_until_it_is_ready(self):
        with mock.patch("xthread2social.targets.fetch_video", return_value=b"MP4DATA"), \
             mock.patch("xthread2social.targets.time.sleep"):
            units = self.tgt.prepare(render(a_video_thread(), "mastodon"))
        self.assertEqual(units[0]["media_ids"], ["77"])
        self.assertEqual(self.calls[0][0], "/api/v2/media")
        self.assertGreater(self.calls[0][1], 120)      # video upload gets the longer timeout
        self.assertIn(b"Content-Type: video/mp4", self.calls[0][2])
        self.assertIn(b'filename="ja0_3fIdy3w7DmxQ.mp4"', self.calls[0][2])
        self.assertEqual(self.calls[1][0], "/api/v1/media/77")

    def test_a_fetch_failure_names_the_escape_hatch(self):
        with mock.patch("xthread2social.targets.fetch_video",
                        side_effect=ValueError("48.0 MB exceeds the 40 MB cap")):
            with self.assertRaises(PostError) as caught:
                self.tgt.prepare(render(a_video_thread(), "mastodon"))
        self.assertIn("--no-video", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
