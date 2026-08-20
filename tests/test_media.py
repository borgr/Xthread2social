"""Image preparation: the 1MB Bluesky blob cap is a real mid-thread failure path."""
import io
import unittest

from thread2social.media import BLUESKY_BLOB_CAP, shrink


def noisy_png(w, h):
    """An incompressible image, so the test actually exercises the resize ladder."""
    import random
    from PIL import Image
    rnd = random.Random(0)
    im = Image.new("RGB", (w, h))
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(w * h)])
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class TestShrink(unittest.TestCase):
    def test_small_image_passes_through_untouched(self):
        data = noisy_png(40, 40)
        out, mime = shrink(data)
        self.assertEqual(out, data)
        self.assertEqual(mime, "image/png")

    def test_oversized_image_is_brought_under_the_cap(self):
        data = noisy_png(1400, 1400)
        self.assertGreater(len(data), BLUESKY_BLOB_CAP)
        out, mime = shrink(data)
        self.assertLessEqual(len(out), BLUESKY_BLOB_CAP)
        self.assertEqual(mime, "image/jpeg")

    def test_result_is_still_a_valid_decodable_image(self):
        from PIL import Image
        out, _ = shrink(noisy_png(1400, 1400))
        self.assertEqual(Image.open(io.BytesIO(out)).mode, "RGB")

    def test_rgba_is_converted_not_crashed(self):
        from PIL import Image
        im = Image.new("RGBA", (1200, 1200), (255, 0, 0, 128))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        big = buf.getvalue() + b"\x00" * BLUESKY_BLOB_CAP     # force the shrink path
        out, mime = shrink(big)
        self.assertLessEqual(len(out), BLUESKY_BLOB_CAP)


if __name__ == "__main__":
    unittest.main()
