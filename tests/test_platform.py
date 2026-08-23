"""The two socket-activation backends, checked without the other OS.

IS_MAC/IS_LINUX are decided at import, so each case reloads the module under a patched
sys.platform. That is the only way to test the Linux units from a Mac (and vice versa) - the
unit *text* is the whole contract with systemd, so it is what gets asserted.
"""
import importlib
import os
import pathlib
import subprocess
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
        self.assertIn("ExecStart=-/opt/bin/x2s-serve", body)   # `-`: see below
        self.assertIn("StandardInput=socket", body)
        self.assertIn("StandardOutput=socket", body)

    def test_a_handler_that_dies_leaves_no_failed_unit_to_reset(self):
        """Without the leading `-`, every browser that hangs up mid-request accumulates a
        failed xthread2social@N.service until someone runs `systemctl reset-failed`."""
        with mock.patch.object(self.m, "serve_binary", return_value="/opt/bin/x2s-serve"):
            self.assertIn("ExecStart=-", self.m.service_unit_body())

    def test_stderr_is_never_the_socket(self):
        """It defaults to inheriting StandardOutput - here the connection - so one traceback
        would be spliced into the HTTP response the userscript parses."""
        with mock.patch.object(self.m, "serve_binary", return_value="/opt/bin/x2s-serve"):
            body = self.m.service_unit_body()
        self.assertIn("StandardError=journal", body)
        self.assertNotIn("StandardError=socket", body)

    def test_print_units_writes_nothing(self):
        with mock.patch.object(self.m, "serve_binary", return_value="/opt/bin/x2s-serve"), \
             mock.patch.object(pathlib.Path, "write_text") as wrote:
            paths = [str(p) for p, _ in self.m.unit_texts()]
        wrote.assert_not_called()
        self.assertEqual(paths, [str(self.m.SOCKET_UNIT), str(self.m.SERVICE_UNIT)])

    def test_status_names_the_command_a_human_can_rerun(self):
        self.assertIn("systemctl --user status xthread2social.socket",
                      self.m.status_command())

    def test_both_units_parse_and_use_only_real_directives(self):
        """systemd-analyze verify cannot run here, so this is the part of it that can: unit
        files are INI, and it *ignores* a key it does not know (`StandarInput=socket` would
        install cleanly and then quietly not pass the socket). Every key is checked against
        the directive list from systemd.socket(5)/systemd.service(5)/systemd.exec(5)."""
        import configparser
        known = {
            "Unit": {"Description", "Documentation", "After", "Requires", "Wants",
                     "PartOf", "ConditionPathExists"},
            "Socket": {"ListenStream", "Accept", "BindIPv6Only", "SocketUser", "SocketMode",
                       "Service", "MaxConnections", "FreeBind"},
            "Service": {"ExecStart", "ExecStop", "Type", "StandardInput", "StandardOutput",
                        "StandardError", "Environment", "EnvironmentFile", "WorkingDirectory",
                        "TimeoutStopSec", "Restart", "User", "Group"},
            "Install": {"WantedBy", "RequiredBy", "Also", "Alias"},
        }
        with mock.patch.object(self.m, "serve_binary", return_value="/opt/bin/x2s-serve"):
            bodies = [self.m.socket_unit_body(), self.m.service_unit_body()]
        for body in bodies:
            cp = configparser.ConfigParser(strict=True)
            cp.optionxform = str                       # systemd keys are case-sensitive
            cp.read_string(body)                       # raises on malformed INI
            for section in cp.sections():
                self.assertIn(section, known, f"unknown section [{section}]")
                for key in cp[section]:
                    self.assertIn(key, known[section], f"unknown directive {key}=")
                    self.assertTrue(cp[section][key], f"{key}= has no value")

    def test_the_template_name_matches_what_accept_yes_requires(self):
        """With Accept=yes, systemd instantiates `<socket prefix>@.service` per connection; a
        service file named without the `@` is simply never started."""
        self.assertEqual(self.m.SOCKET_UNIT.name.removesuffix(".socket") + "@.service",
                         self.m.SERVICE_UNIT.name)


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

    def test_the_trigger_names_one_user_and_needs_no_stored_password(self):
        """Bare `onlogon` fires for *any* user logging in - a second server, same port. /it
        runs with the interactive token, which is what avoids schtasks' password prompt."""
        with mock.patch.dict(os.environ, {"USERNAME": "someone"}), \
             mock.patch.object(self.m, "_schtasks",
                               return_value=mock.Mock(returncode=0, stderr="", stdout="")) as sched, \
             mock.patch.object(self.m, "_port_open", return_value=True), \
             mock.patch.object(pathlib.Path, "write_text"), \
             mock.patch.object(pathlib.Path, "mkdir"), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            self.m.install()
        create = sched.call_args_list[0].args
        self.assertEqual(create[create.index("/ru") + 1], "someone")
        self.assertIn("/it", create)

    def test_a_refused_flag_falls_through_to_a_simpler_task(self):
        """Which flags a given Windows accepts cannot be checked from here, so /create is tried
        in decreasing order of precision rather than once."""
        with mock.patch.dict(os.environ, {"USERNAME": "someone"}), \
             mock.patch.object(self.m, "_schtasks",
                               side_effect=[mock.Mock(returncode=1, stderr="ERROR: /it", stdout=""),
                                            mock.Mock(returncode=1, stderr="ERROR: /np", stdout=""),
                                            mock.Mock(returncode=0, stderr="", stdout="")]) as sched, \
             mock.patch.object(self.m, "_port_open", return_value=True), \
             mock.patch.object(pathlib.Path, "write_text"), \
             mock.patch.object(pathlib.Path, "mkdir"), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            self.m.install()
        tried = [c.args for c in sched.call_args_list]
        self.assertEqual(len(tried), 3)
        self.assertIn("/it", tried[0])
        self.assertIn("/np", tried[1])
        self.assertNotIn("/ru", tried[2])          # last rung: Microsoft's plain onlogon form

    def test_when_every_form_is_refused_the_error_carries_all_of_them(self):
        with mock.patch.dict(os.environ, {"USERNAME": "someone"}), \
             mock.patch.object(self.m, "_schtasks",
                               return_value=mock.Mock(returncode=1, stdout="",
                                                      stderr="ERROR: Access is denied.")), \
             mock.patch.object(pathlib.Path, "write_text"), \
             mock.patch.object(pathlib.Path, "mkdir"), \
             mock.patch.object(self.m.config, "keychain", return_value="tok"):
            with self.assertRaises(RuntimeError) as caught:
                self.m.install()
        msg = str(caught.exception)
        self.assertEqual(msg.count("Access is denied."), 3)
        self.assertIn("elevated Command Prompt", msg)
        self.assertIn("--post", msg)               # the shortcut is optional; the CLI is not

    def test_schtasks_can_never_block_on_a_prompt(self):
        with mock.patch("subprocess.run") as run:
            self.m._schtasks("/query")
        self.assertEqual(run.call_args[1]["stdin"], subprocess.DEVNULL)
        self.assertTrue(run.call_args[1]["timeout"])

    def test_a_launcher_path_with_a_space_is_quoted_for_task_scheduler(self):
        """Task Scheduler splits Task To Run on whitespace, so an unquoted
        `C:\\Users\\First Last\\...` would try to run `C:\\Users\\First`."""
        spaced = pathlib.PurePath("C:/Users/First Last/x.cmd")
        with mock.patch.object(self.m, "LAUNCHER", spaced):
            self.assertEqual(self.m._task_run_value(), f"'{spaced}'")
        plain = pathlib.PurePath("C:/tools/x.cmd")
        with mock.patch.object(self.m, "LAUNCHER", plain):
            self.assertEqual(self.m._task_run_value(), str(plain))


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

    def test_windows_does_not_let_another_process_take_the_port(self):
        """SO_REUSEADDR on Windows lets a second process bind the same address and receive the
        connections - i.e. collect the token the browser sends. Unix keeps it: there it only
        skips TIME_WAIT."""
        from xthread2social import serve
        with mock.patch.object(serve.sys, "platform", "win32"):
            self.assertFalse(serve._server_classes()[0].allow_reuse_address)
        with mock.patch.object(serve.sys, "platform", "linux"):
            self.assertTrue(serve._server_classes()[0].allow_reuse_address)

    def test_a_taken_port_says_so_instead_of_a_traceback(self):
        import socket
        from xthread2social import serve
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            with self.assertRaises(SystemExit) as caught:
                serve.serve_forever(port=held.getsockname()[1])
        self.assertIn("already taken", str(caught.exception))

    def test_the_windows_launcher_command_line_actually_starts_a_server(self):
        """The .cmd runs `<python> -m xthread2social.serve --foreground`. Only pythonw.exe and
        `start ""` are Windows-specific in that line; the rest is checked here for real, so a
        renamed module or a missing __main__ guard cannot go unnoticed until someone installs
        it on Windows."""
        import socket
        import subprocess
        import sys as _sys
        import time
        import urllib.error
        import urllib.request

        with socket.socket() as probe:                # a port nothing else holds
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "XTHREAD2SOCIAL_PORT": str(port)}
        proc = subprocess.Popen([_sys.executable, "-m", "xthread2social.serve", "--foreground"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        try:
            status = None
            for _ in range(100):                      # up to 10s, startup imports atproto
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1)
                except urllib.error.HTTPError as e:
                    status = e.code
                    break
                except OSError:
                    if proc.poll() is not None:
                        self.fail(f"server exited: {proc.communicate()[0][-500:]}")
                    time.sleep(0.1)
            self.assertEqual(status, 403)             # answered, and refused an untokened GET
        finally:
            proc.terminate()
            proc.wait(timeout=10)
