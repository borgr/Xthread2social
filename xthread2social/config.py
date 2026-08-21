"""Credentials from ~/.config/xthread2social/env (not ./.env - the browser shortcut and
the launchd handler run from an arbitrary working directory)."""
import os
import sys
from pathlib import Path

PATH = Path(os.environ.get("XTHREAD2SOCIAL_ENV",
                           Path.home() / ".config/xthread2social/env"))


def load(path=None):
    """Parse KEY=VALUE lines, strip inline `#` comments, and fold into os.environ
    without overriding anything already set in the environment."""
    p = Path(path or PATH)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        out[k.strip()] = v
        os.environ.setdefault(k.strip(), v)
    resolve_secrets()
    return out


SECRETS = ("ATPROTO_APP_PASSWORD", "MASTODON_ACCESS_TOKEN")
KEYCHAIN_SERVICE = "xthread2social"


def secret_store_name():
    """Which OS secret store this machine has, or "" for none.

    macOS ships `security`; on Linux the equivalent is libsecret's `secret-tool`, which talks
    to whichever keyring the desktop session runs (GNOME Keyring, KWallet). With neither, the
    env file's plaintext value is the only source - the tool still works, less privately.
    """
    import shutil
    if sys.platform == "darwin":
        return "security"
    if shutil.which("secret-tool"):
        return "secret-tool"
    return ""


def keychain(name, service=KEYCHAIN_SERVICE):
    """Read a secret from the OS secret store, or "" if absent/unavailable.

    Preferred over the env file: the value never exists as plaintext on disk, and
    storing it needs no editor (so it never passes through a terminal transcript).
    """
    import subprocess
    tool = secret_store_name()
    if not tool:
        return ""
    cmd = (["security", "find-generic-password", "-s", service, "-a", name, "-w"]
           if tool == "security"
           else ["secret-tool", "lookup", "service", service, "account", name])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve_secrets(names=SECRETS):
    """Fill any secret that is unset or the literal `keychain` from the Keychain."""
    found = []
    for name in names:
        cur = os.environ.get(name, "").strip()
        if cur and cur != "keychain":
            continue
        val = keychain(name)
        if val:
            os.environ[name] = val
            found.append(name)
    return found


def store(name, value, service=KEYCHAIN_SERVICE):
    """Write a secret into the OS secret store, replacing any existing entry."""
    import subprocess
    if not value:
        raise ValueError("refusing to store an empty secret")
    tool = secret_store_name()
    if not tool:
        raise RuntimeError(
            "no OS secret store found: install libsecret's secret-tool "
            "(apt install libsecret-tools / dnf install libsecret) so secrets stay out of "
            f"plaintext, or put {name}=<value> in {PATH} and protect it with chmod 600")
    if tool == "security":
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", service,
                            "-a", name, "-w", value], capture_output=True, text=True)
    else:
        # The value goes on stdin, never in argv - a command line is world-readable in /proc.
        r = subprocess.run(["secret-tool", "store", "--label", f"{service} {name}",
                            "service", service, "account", name],
                           input=value, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"secret store write failed ({tool}): {r.stderr.strip()}")
    os.environ[name] = value


def prompt_secret(name, service=KEYCHAIN_SERVICE):
    """Read a secret from the terminal without echoing and store it.

    Done in-process rather than by a shell one-liner: `read -rs -p` is bash-only, and a
    pasted multi-line block lets `read` swallow the wrong line - both produced an empty
    Keychain entry in practice.
    """
    import getpass
    val = getpass.getpass(f"{name} (not echoed): ").strip()
    if not val:
        raise ValueError("nothing entered - Keychain left unchanged")
    store(name, val, service)
    return val


def get(name, default=""):
    return os.environ.get(name, default)
