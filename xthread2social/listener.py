"""Install/uninstall the socket-activated agent for the browser shortcut.

Socket activation rather than a daemon: the init system holds the listening socket and starts
`xthread2social-serve` only when the userscript connects, so nothing runs (or leaks, or needs
restarting after a reboot) between publishes. macOS spells this launchd + `inetdCompatibility`,
Linux spells it a systemd *user* socket unit with `Accept=yes`; both hand the connected socket
to the handler as stdin/stdout, which is why serve.py needs no per-platform code at all.
"""
import os
import plistlib
import secrets
import subprocess
import sys
from pathlib import Path

from . import config

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_WIN = sys.platform == "win32"      # "win32" on 64-bit Windows too

# Names the software, not the machine's owner: the agent is installed verbatim on anyone
# else's machine too, and the old "com.lc." label is booted out and deleted on the next
# --install-listener (two units binding 127.0.0.1:8765 would fight over the socket).
LABEL = "io.github.borgr.xthread2social"
LEGACY_LABELS = ("com.lc.xthread2social",)
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
SOCKET_UNIT = UNIT_DIR / "xthread2social.socket"
SERVICE_UNIT = UNIT_DIR / "xthread2social@.service"   # @: one instance per connection

TASK_NAME = "Xthread2social listener"
WIN_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "xthread2social"
LAUNCHER = WIN_DIR / "start-listener.cmd"

PORT = int(os.environ.get("XTHREAD2SOCIAL_PORT", "8765"))
TOKEN_NAME = "LISTENER_TOKEN"
ERR_LOG = Path.home() / ".local/share/xthread2social/serve.err"


class Unsupported(RuntimeError):
    """No socket-activation backend for this platform."""


def manager():
    """The service manager in use, for messages and diagnosis."""
    if IS_MAC:
        return "launchd"
    if IS_LINUX:
        return "systemd --user"
    if IS_WIN:
        return "schtasks"
    raise Unsupported(
        f"the browser shortcut needs launchd (macOS), systemd (Linux) or Task Scheduler "
        f"(Windows); {sys.platform} has none. The CLI itself works: "
        f"`xthread2social <url> --post`.")


def unit_files():
    """The files install() writes - what to look at when the shortcut is dead."""
    if IS_MAC:
        return [PLIST]
    if IS_WIN:
        return [LAUNCHER]
    return [SOCKET_UNIT, SERVICE_UNIT]


def serve_binary():
    """Absolute path to a real `xthread2social-serve`.

    The service manager gets no PATH and no virtualenv, so the unit names the binary outright.
    A unit pointing at a missing path loads fine and then resets every connection, so the
    candidates are checked for existence here.
    """
    import shutil
    seen = []
    for cand in (Path(sys.executable).parent / "xthread2social-serve",
                 Path(sys.prefix) / "bin/xthread2social-serve",
                 Path(shutil.which("xthread2social-serve") or "/nonexistent"),
                 Path.home() / ".local/bin/xthread2social-serve"):
        if cand.exists():
            return str(cand.resolve())
        seen.append(str(cand))
    raise RuntimeError("cannot find xthread2social-serve; install the package first "
                       "(looked in: " + ", ".join(seen) + ")")


# ---------- launchd (macOS) ----------

def plist_body():
    return {
        "Label": LABEL,
        "ProgramArguments": [serve_binary()],
        "inetdCompatibility": {"Wait": False},
        "Sockets": {"Listener": {"SockNodeName": "127.0.0.1",   # never the LAN
                                 "SockServiceName": str(PORT),
                                 "SockType": "stream",
                                 "SockFamily": "IPv4"}},
        "StandardErrorPath": str(ERR_LOG),
        "ProcessType": "Interactive",
    }


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _install_launchd():
    with open(PLIST, "wb") as fh:
        plistlib.dump(plist_body(), fh)
    uid = os.getuid()
    for old in LEGACY_LABELS:
        _launchctl("bootout", f"gui/{uid}/{old}")
        legacy = PLIST.parent / f"{old}.plist"
        if legacy.exists():
            legacy.unlink()
    _launchctl("bootout", f"gui/{uid}/{LABEL}")            # ignore "not loaded"
    r = _launchctl("bootstrap", f"gui/{uid}", str(PLIST))
    if r.returncode != 0:
        r = _launchctl("load", "-w", str(PLIST))           # older macOS spelling
    if r.returncode != 0:
        raise RuntimeError(f"launchctl refused the agent: {r.stderr.strip() or r.stdout.strip()}")


def _uninstall_launchd():
    uid = os.getuid()
    for old in LEGACY_LABELS:
        _launchctl("bootout", f"gui/{uid}/{old}")
        legacy = PLIST.parent / f"{old}.plist"
        if legacy.exists():
            legacy.unlink()
    _launchctl("bootout", f"gui/{uid}/{LABEL}")
    _launchctl("unload", str(PLIST))
    if PLIST.exists():
        PLIST.unlink()


# ---------- systemd user units (Linux) ----------

def socket_unit_body():
    """`Accept=yes` is inetd mode: one short-lived service instance per connection.

    BindIPv6Only/ListenStream is written as an explicit 127.0.0.1 literal so the socket is
    never reachable from the LAN - the token is a second line of defence, not the first.
    """
    return (f"[Unit]\n"
            f"Description=Xthread2social publish listener (browser shortcut)\n\n"
            f"[Socket]\n"
            f"ListenStream=127.0.0.1:{PORT}\n"
            f"Accept=yes\n\n"
            f"[Install]\n"
            f"WantedBy=sockets.target\n")


def service_unit_body():
    """A template unit: systemd starts xthread2social@<n>.service per accepted connection,
    with the connection itself as stdin/stdout - exactly what launchd's inetdCompatibility
    does, so the handler is identical.

    A browser that hangs up mid-request makes the handler die non-zero, and without the `-`
    prefix on ExecStart each of those leaves a failed instance unit behind until someone runs
    `systemctl reset-failed` - the same reason systemd's own inetd example writes `ExecStart=-`.
    StandardError is set explicitly because it otherwise *inherits StandardOutput*, i.e. the
    connected socket, so one traceback would be spliced into the HTTP response body.
    """
    return (f"[Unit]\n"
            f"Description=Xthread2social publish handler\n\n"
            f"[Service]\n"
            f"ExecStart=-{serve_binary()}\n"
            f"StandardInput=socket\n"
            f"StandardOutput=socket\n"
            f"StandardError=journal\n")


def _systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _install_systemd():
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SOCKET_UNIT.write_text(socket_unit_body())
    SERVICE_UNIT.write_text(service_unit_body())
    _systemctl("daemon-reload")
    _systemctl("stop", SOCKET_UNIT.name)                   # ignore "not loaded"
    r = _systemctl("enable", "--now", SOCKET_UNIT.name)
    if r.returncode != 0:
        raise RuntimeError(
            f"systemctl refused the socket: {r.stderr.strip() or r.stdout.strip()}\n"
            f"On a headless box `systemctl --user` needs a session bus: try "
            f"`loginctl enable-linger $USER`.")


def _uninstall_systemd():
    _systemctl("disable", "--now", SOCKET_UNIT.name)
    for f in (SOCKET_UNIT, SERVICE_UNIT):
        if f.exists():
            f.unlink()
    _systemctl("daemon-reload")


# ---------- Task Scheduler (Windows) ----------
#
# The one platform with no socket activation, so the shape differs: a resident server started
# at logon rather than a handler started per connection. Everything above stays untouched -
# this branch adds a process, it does not change the protocol, and serve.serve_one() is the
# same function both arrangements call.

def launcher_body():
    """A .cmd that starts the resident server with no console window.

    pythonw.exe rather than python.exe: a console script would leave a black window open for
    as long as the listener lives. `start ""` returns immediately so Task Scheduler does not
    hold the task "running" forever and refuse to start it again.
    """
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return (f'@echo off\r\n'
            f'rem Written by `xthread2social --install-listener`; edit nothing here.\r\n'
            f'start "" "{exe}" -m xthread2social.serve --foreground\r\n')


def _schtasks(*args):
    # schtasks can prompt for a run-as password even for the current user, which would hang
    # --install-listener on an inherited console. Closed stdin and a deadline fail instead.
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def _task_run_value():
    """The /tr value: the launcher path, single-quoted only if it contains a space.

    Task Scheduler splits "Task To Run" on whitespace into program and arguments, so a path
    under `C:\\Users\\First Last\\…` needs quoting or the task runs `C:\\Users\\First`.
    Single quotes, because subprocess's Windows quoting escapes double quotes into the value.
    """
    p = str(LAUNCHER)
    return f"'{p}'" if " " in p else p


def _port_open():
    """Is something actually accepting on the port?

    Only Windows needs it. Socket activation makes "installed" imply "answers", while a
    resident server can be registered at logon and dead now.
    """
    import socket as _s
    try:
        with _s.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except OSError:
        return False


def _task_variants():
    """The /create argument lists to try, most correct first.

    /ru can be refused where policy forbids naming a run-as account, and /it where the
    principal has no interactive logon right, so each rung drops a flag the one above it may
    have been rejected for. The last rung is Microsoft's plain onlogon form.
    """
    user = os.environ.get("USERNAME") or ""
    base = ("/create", "/tn", TASK_NAME, "/sc", "onlogon")
    tail = ("/tr", _task_run_value(), "/f")
    rungs = []
    if user:
        rungs.append((*base, "/ru", user, "/it", *tail))     # one user, interactive token
        rungs.append((*base, "/ru", user, "/np", *tail))     # no stored password, no console
    rungs.append((*base, *tail))                             # any user logging on
    return rungs


def _install_windows():
    WIN_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(launcher_body())
    failures = []
    for argv in _task_variants():
        r = _schtasks(*argv)
        if r.returncode == 0:
            break
        failures.append(f"{' '.join(argv[5:-3])or 'plain onlogon'}: "
                        f"{(r.stderr or r.stdout).strip()}")
    else:
        raise RuntimeError(
            "schtasks refused the task in every form:\n  " + "\n  ".join(failures) +
            "\nIf it says access is denied, run `xthread2social --install-listener` from an "
            "elevated Command Prompt (some policies allow only administrators to add tasks). "
            "The CLI itself works without any of this: `xthread2social <url> --post`.")
    if not _port_open():
        _schtasks("/run", "/tn", TASK_NAME)       # start it now, not at the next logon


def _uninstall_windows():
    _schtasks("/end", "/tn", TASK_NAME)
    _schtasks("/delete", "/tn", TASK_NAME, "/f")
    if LAUNCHER.exists():
        LAUNCHER.unlink()


# ---------- public API ----------

def install(rotate=False):
    """Write the unit(s), (re)load them, and return the token the browser must send."""
    manager()                                              # raises Unsupported early
    token = config.keychain(TOKEN_NAME)
    if rotate or not token:
        token = secrets.token_urlsafe(24)
        config.store(TOKEN_NAME, token)
    ERR_LOG.parent.mkdir(parents=True, exist_ok=True)
    unit_files()[0].parent.mkdir(parents=True, exist_ok=True)
    backends = {"launchd": _install_launchd, "systemd --user": _install_systemd,
                "schtasks": _install_windows}
    backends[manager()]()
    return token


def uninstall():
    backends = {"launchd": _uninstall_launchd, "systemd --user": _uninstall_systemd,
                "schtasks": _uninstall_windows}
    backends[manager()]()


def status():
    """(loaded, token_present) - enough to tell 'not installed' from 'wrong token'."""
    if IS_MAC:
        loaded = _launchctl("list", LABEL).returncode == 0
    elif IS_LINUX:
        loaded = _systemctl("is-active", SOCKET_UNIT.name).stdout.strip() == "active"
    elif IS_WIN:
        # Registered *and* answering: see _port_open() for why the second half is needed.
        loaded = _schtasks("/query", "/tn", TASK_NAME).returncode == 0 and _port_open()
    else:
        loaded = False
    return loaded, bool(config.keychain(TOKEN_NAME))


def unit_texts():
    """[(path, text)] for what install() would write, without writing anything.

    Lets a first Linux user run `xthread2social --print-units | systemd-analyze verify
    /dev/stdin`, or a Windows user read the .cmd, before installing either.
    """
    if IS_MAC:
        return [(PLIST, plistlib.dumps(plist_body()).decode())]
    if IS_WIN:
        return [(LAUNCHER, launcher_body()),
                (Path("schtasks"), "schtasks /create /tn \"%s\" /sc onlogon /ru %s /it "
                                   "/tr %s /f\n" % (TASK_NAME,
                                                    os.environ.get("USERNAME") or "",
                                                    _task_run_value()))]
    return [(SOCKET_UNIT, socket_unit_body()), (SERVICE_UNIT, service_unit_body())]


def status_command():
    """The command a human should run to see the same thing status() saw."""
    if IS_MAC:
        return f"launchctl list {LABEL}"
    if IS_WIN:
        return f'schtasks /query /tn "{TASK_NAME}" /v'
    return f"systemctl --user status {SOCKET_UNIT.name}"
