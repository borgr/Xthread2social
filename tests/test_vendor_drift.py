"""Guard the vendored copy of relay.py's splitter against upstream drift.

Vendoring was deliberate (this tool must never import a module that can reach the
colab-links credentials), but a silent divergence is the known failure mode, so compare
the functions when the upstream checkout is present and skip when it isn't.
"""
import pathlib
import unittest

import thread2social.textutil as tu

UPSTREAM = pathlib.Path.home() / "PycharmProjects/discord_atproto_bridge/relay.py"
FUNCS = ["display_for", "_default_url_eff", "_url_reductions", "_eff",
         "_is_sentence_end", "_boundaries", "chunk_text", "build_richtext"]


def extract(text, name):
    start = text.index(f"def {name}(")
    rest = text[start:]
    end = len(rest)
    for i, line in enumerate(rest.splitlines(keepends=True)):
        if i and line.strip() and not line[0].isspace():
            end = sum(len(x) for x in rest.splitlines(keepends=True)[:i])
            break
    return rest[:end].rstrip()


class TestVendorDrift(unittest.TestCase):
    @unittest.skipUnless(UPSTREAM.exists(), "upstream relay.py not checked out here")
    def test_vendored_functions_match_upstream(self):
        up = UPSTREAM.read_text()
        mine = pathlib.Path(tu.__file__).read_text()
        for name in FUNCS:
            self.assertEqual(extract(mine, name), extract(up, name),
                             f"{name} has drifted from relay.py — re-vendor deliberately")

    def test_local_additions_are_present(self):
        for name in ("glen", "chunk_for_bluesky", "mastodon_eff"):
            self.assertTrue(hasattr(tu, name))


if __name__ == "__main__":
    unittest.main()
