"""Run the Windows secret store's ctypes code for real, without Windows.

`tests/fixtures/dpapi_stub.c` exports CryptProtectData/CryptUnprotectData/LocalFree with the
documented DATA_BLOB layout and ownership rule, so everything on our side of the boundary -
struct layout, byref, the plaintext buffer's lifetime, string_at, the free - is executed here.
What this cannot check is Microsoft's half: real per-user encryption, and the stdcall/cdecl
distinction that only exists on 32-bit Windows (on x64 the two conventions are one).
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xthread2social import config

STUB_C = Path(__file__).parent / "fixtures/dpapi_stub.c"


def build_stub(into):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise unittest.SkipTest("no C compiler, so the DPAPI stub cannot be built")
    lib = Path(into) / ("dpapi_stub" + (".dylib" if sys.platform == "darwin" else ".so"))
    r = subprocess.run([cc, "-shared", "-fPIC", "-O0", "-o", str(lib), str(STUB_C)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise unittest.SkipTest(f"stub did not compile: {r.stderr.strip()[:200]}")
    return ctypes.CDLL(str(lib), use_last_error=True)


class TestDpapiMarshalling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = build_stub(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # Both handles are the same object: the stub carries crypt32's and kernel32's symbols.
        self.libs = mock.patch.object(config, "_dpapi_libs",
                                      return_value=(self.lib, self.lib))
        self.libs.start()
        self.addCleanup(self.libs.stop)

    def test_a_secret_survives_the_round_trip(self):
        blob = config._dpapi(b"app-password-1234", encrypt=True)
        self.assertNotIn(b"app-password", blob)
        self.assertEqual(config._dpapi(blob, encrypt=False), b"app-password-1234")

    def test_a_long_secret_is_not_truncated(self):
        """cbData is 32 bits wide; a c_ulong field would be 8 bytes off-Windows and the length
        would be read out of the wrong half of the struct."""
        secret = bytes(range(256)) * 4
        self.assertEqual(config._dpapi(config._dpapi(secret, encrypt=True), encrypt=False),
                         secret)

    def test_every_length_round_trips(self):
        """Lengths near zero are where an off-by-one in the buffer or cbData shows up."""
        for n in (0, 1, 2, 3, 15, 16, 17):
            with self.subTest(n=n):
                data = b"x" * n
                self.assertEqual(
                    config._dpapi(config._dpapi(data, encrypt=True), encrypt=False), data)

    def test_a_foreign_blob_raises_instead_of_returning_garbage(self):
        with self.assertRaises(RuntimeError) as caught:
            config._dpapi(b"not a DPAPI blob", encrypt=False)
        self.assertIn("decrypt", str(caught.exception))

    def test_the_plaintext_buffer_outlives_the_call(self):
        """`BLOB(len, cast(create_string_buffer(v), …))` leaves the buffer unreferenced, so a
        collection between building the struct and the call can free the plaintext. Collecting
        around every call widens that window as far as it goes."""
        import gc
        gc.set_debug(0)
        was_on = gc.isenabled()
        gc.enable()
        try:
            for _ in range(50):
                gc.collect()
                blob = config._dpapi(b"survive the collector", encrypt=True)
                gc.collect()
                self.assertEqual(config._dpapi(blob, encrypt=False), b"survive the collector")
        finally:
            if not was_on:
                gc.disable()


class TestDpapiSecretStore(unittest.TestCase):
    """The file layer above _dpapi: one user-encrypted blob per secret under %LOCALAPPDATA%."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = build_stub(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for p in (mock.patch.object(sys, "platform", "win32"),
                  mock.patch.dict(os.environ, {"LOCALAPPDATA": self.home.name}),
                  mock.patch.object(config, "_dpapi_libs",
                                    return_value=(self.lib, self.lib))):
            p.start()
            self.addCleanup(p.stop)

    def test_store_then_read_back(self):
        config.store("ATPROTO_APP_PASSWORD", "abcd-efgh-ijkl")
        self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "abcd-efgh-ijkl")

    def test_the_secret_is_not_on_disk_in_the_clear(self):
        config.store("MASTODON_ACCESS_TOKEN", "tok3n-value")
        blobs = list(Path(self.home.name).rglob("*.bin"))
        self.assertEqual(len(blobs), 1)                      # one file per secret
        self.assertNotIn(b"tok3n-value", blobs[0].read_bytes())

    def test_an_unset_secret_reads_as_absent(self):
        self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "")

    def test_a_corrupt_blob_reads_as_absent_rather_than_crashing_a_publish(self):
        config.store("ATPROTO_APP_PASSWORD", "abcd")
        blob = next(Path(self.home.name).rglob("*.bin"))
        blob.write_bytes(b"truncated")
        self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "")

    def test_rotating_replaces_the_blob(self):
        config.store("ATPROTO_APP_PASSWORD", "first")
        config.store("ATPROTO_APP_PASSWORD", "second")
        self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "second")
        self.assertEqual(len(list(Path(self.home.name).rglob("*.bin"))), 1)


if __name__ == "__main__":
    unittest.main()
