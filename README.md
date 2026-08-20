# thread2social

Republish an X thread to **your own** Bluesky and Mastodon accounts. No API keys, no server,
no paid tier. Design and rationale: [PLAN.md](PLAN.md).

```bash
thread2social https://x.com/someone/status/<last-tweet>          # preview (default)
thread2social <last-tweet-url> <first-tweet-url> --post          # publish
```

Give it the **last** tweet of the thread; it walks backwards to the first. Adding the first
tweet's URL too (either order) upgrades the completeness check from a warning to a proof.

## Install (works the same on a new machine)

The Python tool must be installed on each machine — it needs local Python and the local
Keychain. Nothing but the code is machine-specific, so it is a clone plus one install:

```bash
git clone https://github.com/borgr/thread2social ~/PycharmProjects/thread2social
python3 -m venv ~/.venvs/thread2social
~/.venvs/thread2social/bin/pip install -e ~/PycharmProjects/thread2social regex
ln -s ~/.venvs/thread2social/bin/thread2social ~/.local/bin/thread2social   # must be on PATH
```

`pipx install -e ~/PycharmProjects/thread2social` does the same in one line where pipx exists.
The userscript is **not** installed from the clone — see [Browser shortcut](#browser-shortcut).

Bare install needs only `atproto` and `pillow`. `pip install 'thread2social[graphemes]'` adds
`regex`, which makes Bluesky's 300-**grapheme** limit exact instead of approximated.

## Configure

`~/.config/thread2social/env` (not a repo-local `.env`, so the browser shortcut works from any
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
thread2social --set-secret bluesky      # or: --set-secret mastodon
```

It prompts without echoing, stores the value, and immediately verifies it by logging in.
Rotating is the same command. Avoid shell `read` one-liners for this: `read -rs -p` is
bash-only, and pasting a multi-line block lets `read` swallow the wrong line — both failed
here, the second time storing an empty secret silently.

Rotating is the same command with a new value. `thread2social --check` confirms it worked.

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
- Adds `— via @author <source>` unless the thread is yours (`--no-attribution` to drop it).

## Useful flags

| Flag | Effect |
|---|---|
| `--post` | actually publish (everything is preview-only by default) |
| `--to bluesky` / `--to mastodon` | one target instead of both |
| `--alt` | prompt for alt text per image (X's endpoint does not expose it) |
| `--allow-incomplete` | post even though the last tweet may have a continuation |
| `--save-json f` / `--from-json f` | dump or reuse the parsed thread; `-` reads stdin |

## Browser shortcut

Install it **from the URL**, not from your disk — that way a new machine gets it with one
click and edits you push here reach every browser you use:

<https://raw.githubusercontent.com/borgr/thread2social/main/userscript/thread2social.user.js>

Opening that link with Tampermonkey installed shows its install screen. `@updateURL` points at
the same file, so Tampermonkey re-checks it and offers updates when `@version` in the header
goes up — bump that line whenever you change the script, or nothing will update. (Importing the
local file also works, but pins that browser to whatever the file said the day you imported it.)

On a thread press
**Ctrl/Cmd+Shift+T** and it copies a ready-to-run `thread2social …` command for the thread
you're looking at. Paste it into a terminal: you get the preview, then `y` publishes. It
collects tweet ids only — no parsing, no credentials in the browser.
Scroll to the end of the thread first; the CLI still re-walks and re-verifies the chain.

An extension is required rather than a bookmarklet: x.com sends `default-src 'self'`, so
page-context requests to anything else are blocked.

## When it breaks

The syndication endpoint is undocumented (it powers Vercel's `react-tweet`). If it stops
serving, `thread2social --from-json` still accepts a thread from any source — that's the
fallback path, and the reason `thread.json` is the only interface between reading and posting.

```bash
python3 -m unittest discover -s tests      # 43 tests, all offline
```
