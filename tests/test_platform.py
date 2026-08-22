"""The two socket-activation backends, checked without the other OS.

IS_MAC/IS_LINUX are decided at import, so each case reloads the module under a patched
sys.platform. That is the only way to test the Linux units from a Mac (and vice versa) - the
unit *text* is the whole contract with systemd, so it is what gets asserted.
"""
import importlib
import pathlib
import sys
import unittest
from unittest import mock

from xthread2social import listener as real_listener


def reload_as(platform):
    with mock.patch.object(sys, "platform", platform):
        mod = importlib.reload(real_listener)
        mod._platform_for_test = platform
        return mod


class TestBackendSelection(unittest.TestCase):
    def tearDown(self):
        reload_as(sys.platform)                  # leave the module as this machine's

    def test_macos_installs_one_launchd_plist(self):
        m = reload_as("darwin")
        self.assertEqual(m.manager(), "launchd")
        self.assertEqual([f.name for f in m.unit_files()],
                         [f"{m.LABEL}.plist"])

    def test_linux_installs_a_socket_and_a_template_service(self):
        m = reload_as("linux")
        self.assertEqual(m.manager(), "systemd --user")
        self.assertEqual([f.name for f in m.unit_files()],
                         ["xthread2social.socket", "xthread2social@.service"])

    def test_windows_installs_one_launcher_script(self):
        m = reload_as("win32")
        self.assertEqual(m.manager(), "schtasks")
        self.assertEqual([f.name for f in m.unit_files()], ["start-listener.cmd"])

    def test_an_unsupported_platform_says_so_and_points_at_the_cli(self):
        m = reload_as("sunos5")
        with self.assertRaises(m.Unsupported) as caught:
            m.manager()
        self.assertIn("xthread2social", str(caught.exception))
        self.assertFalse(m.status()[0])

    def test_install_dispatches_to_the_platform_backend(self):
        for platform, expect in (("darwin", "_install_launchd"), ("linux", "_install_systemd"),
                                 ("win32", "_install_windows")):
            m = reload_as(platform)
            with mock.patch.object(m, expect) as backend, \
                 mock.patch.object(m.config, "keychain", return_value="tok"):
                m.install()
            backend.assert_called_once()


class TestSystemdUnits(unittest.TestCase):
    def setUp(self):
        self.m = reload_as("linux")

    def tearDown(self):
        reload_as(sys.platform)

    def test_the_socket_is_bound_to_loopback_only(self):
        body = self.m.socket_unit_body()
        self.assertIn(f"ListenStream=127.0.0.1:{self.m.PORT}", body)
        self.assertNotIn("ListenStream=%s\n" % self.m.PORT, body)   # never all interfaces

    def test_accept_yes_is_what_makes_it_inetd_style(self):
        self.assertIn("Accept=yes", self.m.socket_unit_body())
        self.assertIn("WantedBy=sockets.target", self.m.socket_unit_body())

    def test_the_service_gets_the_connection_as_stdin_and_stdout(self):
        with mock.patch.object(self.m, "serve_binary", return_value="/opt/bin/x2s-serve"):
            body = self.m.service_unit_body()
        self.assertIn("ExecStart=/opt/bin/x2s-serve", body)
        self.assertIn("StandardInput=socket", body)
        self.assertIn("StandardOutput=socket", body)

    def test_status_names_the_command_a_human_can_rerun(self):
        self.assertIn("systemctl --user status xthread2social.socket",
                      self.m.status_command())


class TestSecretStore(unittest.TestCase):
    def test_linux_without_secret_tool_reads_nothing_rather_than_crashing(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("shutil.which", return_value=None):
            self.assertEqual(config.secret_store_name(), "")
            self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "")

    def test_storing_without_a_secret_store_explains_both_ways_out(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                config.store("ATPROTO_APP_PASSWORD", "x")
        msg = str(caught.exception)
        self.assertIn("secret-tool", msg)
        self.assertIn("chmod 600", msg)

    def test_the_value_never_appears_in_argv_on_linux(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            config.store("ATPROTO_APP_PASSWORD", "s3cret")
        argv, kwargs = run.call_args[0][0], run.call_args[1]
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertEqual(kwargs["input"], "s3cret")


if __name__ == "__main__":
    unittest.main()


class TestWindowsBackend(unittest.TestCase):
    def setUp(self):
        self.m = reload_as("win32")

    def tearDown(self):
        reload_as(sys.platform)

    def test_the_launcher_hides_the_console_and_returns_immediately(self):
        body = self.m.launcher_body()
        self.assertIn("-m xthread2social.serve --foreground", body)
        self.assertIn('start ""', body)          # schtasks must not see a task that never ends
        self.assertTrue(body.endswith("\r\n"))   # CRLF: a .cmd with LF endings misparses

    def test_it_prefers_pythonw_so_no_black_window_stays_open(self):
        with mock.patch.object(pathlib.Path, "exists", lambda self: True):
            self.assertIn("pythonw.exe", self.m.launcher_body())

    def test_registered_but_dead_counts_as_not_loaded(self):
        """The failure mode socket activation cannot have: a task that ran and then died."""
        with mock.patch.object(self.m, "_schtasks",
                               return_value=mock.Mock(returncode=0)), \
             mock.patch.object(self.m, "_port_open", return_value=False), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            self.assertEqual(self.m.status(), (False, True))

    def test_registered_and_answering_is_loaded(self):
        with mock.patch.object(self.m, "_schtasks",
                               return_value=mock.Mock(returncode=0)), \
             mock.patch.object(self.m, "_port_open", return_value=True), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            self.assertEqual(self.m.status(), (True, True))

    def test_install_registers_an_onlogon_task_and_starts_it_now(self):
        with mock.patch.object(self.m, "_schtasks",
                               return_value=mock.Mock(returncode=0, stderr="", stdout="")) as sched, \
             mock.patch.object(self.m, "_port_open", return_value=False), \
             mock.patch.object(pathlib.Path, "write_text"), \
             mock.patch.object(pathlib.Path, "mkdir"), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            self.m.install()
        flags = [c.args for c in sched.call_args_list]
        self.assertIn("onlogon", flags[0])
        self.assertIn("/run", flags[-1])


class TestDpapi(unittest.TestCase):
    def test_windows_uses_dpapi_without_a_new_dependency(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(config.secret_store_name(), "dpapi")

    def test_each_secret_is_one_user_encrypted_file(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "win32"):
            path = config._dpapi_path("ATPROTO_APP_PASSWORD", "xthread2social")
        self.assertTrue(str(path).endswith("xthread2social_ATPROTO_APP_PASSWORD.bin"))
        self.assertIn("secrets", path.parts)

    def test_an_unreadable_blob_reads_as_absent_rather_than_raising(self):
        from xthread2social import config
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(config, "_dpapi", side_effect=RuntimeError("bad blob")), \
             mock.patch.object(pathlib.Path, "read_bytes", return_value=b"junk"):
            self.assertEqual(config.keychain("ATPROTO_APP_PASSWORD"), "")


class TestForegroundServer(unittest.TestCase):
    """The resident mode Windows needs, exercised where it can actually be run.

    This is the point of routing both modes through serve_one(): the Windows arrangement is
    unverifiable from here, but the code it depends on is not.
    """

    def test_it_answers_a_real_request_over_a_real_socket(self):
        import json
        import socket
        import threading
        from xthread2social import serve

        started = threading.Event()
        box = {}

        def ready(srv):
            box["srv"] = srv
            started.set()

        with mock.patch.object(serve, "handle",
                               return_value=(200, {"ok": True, "seen": "yes"})):
            t = threading.Thread(target=serve.serve_forever,
                                 kwargs={"port": 0, "ready": ready}, daemon=True)
            t.start()
            self.assertTrue(started.wait(5), "server never bound")
            port = box["srv"].server_address[1]
            body = json.dumps({"urls": ["u"]}).encode()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(b"POST /preview HTTP/1.1\r\nHost: x\r\nContent-Length: " +
                          str(len(body)).encode() + b"\r\n\r\n" + body)
                raw = b""
                while chunk := s.recv(4096):
                    raw += chunk
            box["srv"].shutdown()
        got = json.loads(raw.partition(b"\r\n\r\n")[2])
        self.assertEqual(got, {"ok": True, "seen": "yes"})
        self.assertIn(b"200 OK", raw)
