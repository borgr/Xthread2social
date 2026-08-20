# Xthread2social

Republish an X thread to **your own** Bluesky and Mastodon accounts. No API keys, no server,
no paid tier. Design and rationale: [PLAN.md](PLAN.md).

```bash
xthread2social https://x.com/someone/status/<last-tweet>          # preview (default)
xthread2social <last-tweet-url> <first-tweet-url> --post          # publish
```

Give it the **last** tweet of the thread; it walks backwards to the first. Adding the first
tweet's URL too (either order) upgrades the completeness check from a warning to a proof.

## Install (works the same on a new machine)

The Python tool must be installed on each machine — it needs local Python and the local
Keychain. Nothing but the code is machine-specific, so it is a clone plus one install:

```bash
git clone https://github.com/borgr/Xthread2social ~/PycharmProjects/Xthread2social
python3 -m venv ~/.venvs/xthread2social
~/.venvs/xthread2social/bin/pip install -e ~/PycharmProjects/Xthread2social regex
ln -s ~/.venvs/xthread2social/bin/xthread2social ~/.local/bin/xthread2social   # must be on PATH
```

`pipx install -e ~/PycharmProjects/Xthread2social` does the same in one line where pipx exists.
The userscript is **not** installed from the clone — see [Browser shortcut](#browser-shortcut).

Bare install needs only `atproto` and `pillow`. `pip install 'xthread2social[graphemes]'` adds
`regex`, which makes Bluesky's 300-**grapheme** limit exact instead of approximated.

## Configure

`~/.config/xthread2social/env` (not a repo-local `.env`, so the browser shortcut works from any
directory). Both targets are optional — an unconfigured one is skipped, not fatal.

```ini
ATPROTO_HANDLE=you.bsky.social
ATPROTO_APP_PASSWORD=keychain                # see below
MASTODON_BASE_URL=https://your.instance
MASTODON_ACCESS_TOKEN=keychain
```

Secrets set to the literal `keychain` (or left blank) are read from the macOS Keychain, so
they never exist as plaintext on disk. Store one without it passing through an editor or a
terminal transcript:

The Bluesky secret is an **app password** (Settings → Privacy and security → App passwords),
never your account password. The Mastodon secret is an access token from your instance's
Preferences → Development → New application, with scopes `read:accounts`, `write:statuses`,
`write:media` and nothing else — `read:accounts` only reads your own profile, and is what lets
`--check` verify the token before a publish rather than during one.

```bash
xthread2social --set-secret bluesky      # or: --set-secret mastodon
```

It prompts without echoing, stores the value, and immediately verifies it by logging in.
Rotating is the same command. Avoid shell `read` one-liners for this: `read -rs -p` is
bash-only, and pasting a multi-line block lets `read` swallow the wrong line — both failed
here, the second time storing an empty secret silently.

Rotating is the same command with a new value. `xthread2social --check` confirms it worked.

These are separate from the `discord_atproto_bridge` relay's credentials on purpose: this tool
must not be able to post as `colab-links`.

## What it does

- Walks the thread through X's public syndication endpoint — no auth, no browser.
- Refuses to post unless the chain is contiguous, single-author, and reaches the declared
  first tweet. Half-published threads are the failure this is built to prevent.
- Splits over-long tweets at sentence boundaries (300 graphemes Bluesky / 500 chars Mastodon),
  keeps links clickable, carries images on the post where they appeared.
- Uploads **all** media before creating **any** post, so an image failure publishes nothing.
- Records progress per `(thread, target)`: re-run the same command to resume mid-thread.
- Mastodon root is public, replies unlisted (`--all-public` to override).
- Credits someone else's thread twice: `🔁 crossposted from @author` at the bottom of the
  first post, and `— via @author <source>` on the last. The opening line shrinks to
  `🔁 via @author` or `🔁 @author`, or is dropped entirely, rather than pushing the author's
  opening tweet into two posts. Neither appears on your own threads; `--no-attribution`
  drops both.

## Useful flags

| Flag | Effect |
|---|---|
| `--post` | actually publish (everything is preview-only by default) |
| `--to bluesky` / `--to mastodon` | one target instead of both |
| `--alt` | prompt for alt text per image (X's endpoint does not expose it) |
| `--allow-incomplete` | post even though the last tweet may have a continuation |
| `--save-json f` / `--from-json f` | dump or reuse the parsed thread; `-` reads stdin |
| `--install-listener` | install the launchd agent for the browser shortcut, print its token |
| `--uninstall-listener` | remove that agent |

## Browser shortcut (no terminal)

One-time setup:

```bash
xthread2social --install-listener       # prints a token; installs a launchd agent
```

Then install the userscript **from the URL** (not from your disk, so a new machine gets it
with one click and pushed edits reach every browser):

<https://raw.githubusercontent.com/borgr/Xthread2social/main/userscript/xthread2social.user.js>

Opening that link with Tampermonkey installed shows its install screen. On the first use it
asks for the token above (also under Tampermonkey's menu → *Set publish token*); it is stored
per-browser, which is why this public file contains no secret.

Now, on any X thread, scroll to its last tweet and either click the **🔁 publish** button in
the bottom-right corner or press the hotkey (**Option+Shift+X** by default). An overlay shows
the thread as it will be posted, per target, with warnings; **Publish** posts it and shows the
links. Nothing leaves the browser and no terminal is involved.

**When a hotkey does nothing, it is taken.** Chrome (`Cmd+Shift+T` = reopen closed tab) and
other extensions (1Password grabs `Ctrl+Shift+X`) consume the keystroke before any page script
sees it, and nothing in a userscript can outrank them. Two ways out:

- Tampermonkey menu → *Change hotkey*, then press the combination you want. It is stored
  per-browser, so it survives script updates.
- Free the combination you'd rather use: `chrome://extensions/shortcuts` lists every
  extension-level binding; clear or reassign the one holding it. 1Password's is on that page.

The button never has this problem, which is why it is there. Tampermonkey menu →
*Self-test (is it loaded?)* reports the running version, how many tweets it can see on the
page, whether the token is stored, and whether the listener answers — the first thing to check
if a press does nothing.

How it works: the script collects tweet ids only and calls a listener on `127.0.0.1:8765`,
which `launchd` starts **on connection** and reaps afterwards — no daemon, nothing to restart
after a reboot. Requests must carry the token, so no other page can make you publish, and the
socket is bound to loopback only. `--uninstall-listener` removes the agent;
`--install-listener --rotate-token` issues a new token (and invalidates the old one).

An extension is required rather than a bookmarklet: x.com sends `default-src 'self'`, so
page-context requests to `127.0.0.1` are blocked. Tampermonkey's `GM_xmlhttpRequest` is exempt.

If the listener is ever down, Tampermonkey's menu → *Copy CLI command (fallback)* puts a
ready-to-run command on the clipboard for a terminal.

## When it breaks

The syndication endpoint is undocumented (it powers Vercel's `react-tweet`). If it stops
serving, `xthread2social --from-json` still accepts a thread from any source — that's the
fallback path, and the reason `thread.json` is the only interface between reading and posting.

```bash
python3 -m unittest discover -s tests      # 43 tests, all offline
```
