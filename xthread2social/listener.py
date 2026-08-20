"""Install/uninstall the socket-activated launchd agent for the browser shortcut.

Socket activation rather than a daemon: launchd holds the listening socket and starts
`xthread2social-serve` only when the userscript connects, so nothing runs (or leaks, or
needs restarting after a reboot) between publishes.
"""
import os
import plistlib
import secrets
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "com.lc.xthread2social"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
PORT = int(os.environ.get("XTHREAD2SOCIAL_PORT", "8765"))
TOKEN_NAME = "LISTENER_TOKEN"


def serve_binary():
    """The `xthread2social-serve` next to the running interpreter, so the agent uses the
    same virtualenv as the CLI you installed - not whatever python launchd would find."""
    cand = Path(sys.executable).parent / "xthread2social-serve"
    return str(cand if cand.exists() else Path(sys.prefix) / "bin/xthread2social-serve")


def plist_body():
    return {
        "Label": LABEL,
        "ProgramArguments": [serve_binary()],
        "inetdCompatibility": {"Wait": False},
        "Sockets": {"Listener": {"SockNodeName": "127.0.0.1",   # never the LAN
                                 "SockServiceName": str(PORT),
                                 "SockType": "stream",
                                 "SockFamily": "IPv4"}},
        "StandardErrorPath": str(Path.home() / ".local/share/xthread2social/serve.err"),
        "ProcessType": "Interactive",
    }


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(rotate=False):
    """Write the plist, (re)load it, and return the token the browser must send."""
    token = config.keychain(TOKEN_NAME)
    if rotate or not token:
        token = secrets.token_urlsafe(24)
        config.store(TOKEN_NAME, token)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    Path(plist_body()["StandardErrorPath"]).parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as fh:
        plistlib.dump(plist_body(), fh)
    uid = os.getuid()
    _launchctl("bootout", f"gui/{uid}/{LABEL}")            # ignore "not loaded"
    r = _launchctl("bootstrap", f"gui/{uid}", str(PLIST))
    if r.returncode != 0:
        r = _launchctl("load", "-w", str(PLIST))           # older macOS spelling
    if r.returncode != 0:
        raise RuntimeError(f"launchctl refused the agent: {r.stderr.strip() or r.stdout.strip()}")
    return token


def uninstall():
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    _launchctl("unload", str(PLIST))
    if PLIST.exists():
        PLIST.unlink()


def status():
    """(loaded, token_present) - enough to tell 'not installed' from 'wrong token'."""
    r = _launchctl("list", LABEL)
    return r.returncode == 0, bool(config.keychain(TOKEN_NAME))
