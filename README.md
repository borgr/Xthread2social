# Xthread2social

Republish an X thread to **your own** Bluesky and Mastodon accounts. No API keys, no server,
no paid tier. Design and rationale: [PLAN.md](PLAN.md).

```bash
xthread2social https://x.com/someone/status/<last-tweet>          # preview (default)
xthread2social <last-tweet-url> <first-tweet-url> --post          # publish
```

Give it the **last** tweet of the thread; it walks backwards to the first. Adding the first
tweet's URL too (either order) upgrades the completeness check from a warning to a proof.

## Platforms

macOS and Linux. The publishing engine is pure Python; the two platform-specific pieces are
the secret store (Keychain / libsecret) and the socket-activated listener behind the browser
shortcut (launchd / `systemd --user`). Both are installed by the same `--install-listener`,
which picks the backend for you. Windows runs the CLI but not the listener: it has no socket
activation, so the shortcut would need a permanently running local server — not built.

**The Linux path is written but has not been run on a Linux box** (its unit text is covered by
tests; the live end-to-end is macOS-only so far). If you are the first to try it,
`xthread2social --doctor` names the layer that broke, and `journalctl --user -u 'xthread2social@*'`
has the handler's own errors.

## Install (works the same on a new machine)

The Python tool must be installed on each machine — it needs local Python and the local
secret store. Nothing but the code is machine-specific, so it is a clone plus one install:

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
X_HANDLES=yourxname                          # optional; your own threads then go uncredited
```

Secrets set to the literal `keychain` (or left blank) are read from the OS secret store, so
they never exist as plaintext on disk — the macOS Keychain, or libsecret on Linux (`secret-tool`,
from `libsecret-tools`, talking to GNOME Keyring or KWallet). With neither installed the env
file's own value is used, so `chmod 600` it. Store one without it passing through an editor or a
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
- Holds a per-thread lock while publishing, so two tabs (or a retry after the browser gave up
  on a slow upload) cannot both start from "nothing posted yet" and publish it twice.
- Mastodon root is public, replies unlisted (`--all-public` to override).
- Credits someone else's thread twice: `🔁 x-post from @author` at the bottom of the **first**
  post, and `— x-post from @author <source>` on the last. The opening line always appears —
  it shrinks to `🔁 x-post @author` or `🔁 @author` to avoid costing an extra post, and takes
  the extra post if even the shortest wording will not fit. `--no-attribution` drops both.
- **Your own thread carries nothing at all** — no credit line, no self-reference, and no link
  back to X: reposting yourself is just posting. `--source-link` adds the bare tweet URL back
  when you do want to point at the original. Which handles count as you comes from `X_HANDLES`
  in the env file (comma-separated, `@` optional); with it unset, the Bluesky handle's first
  label is used, so set it if your X and Bluesky names differ — otherwise your own threads get
  credited to you as though a stranger wrote them.

## Useful flags

| Flag | Effect |
|---|---|
| `--post` | actually publish (everything is preview-only by default) |
| `--to bluesky` / `--to mastodon` | one target instead of both |
| `--alt` | prompt for alt text per image (X's endpoint does not expose it) |
| `--note TEXT` | your own words above the author's text on the first post |
| `--source-link` | on your own thread, link back to the tweet (off by default) |
| `--no-attribution` | drop the credit lines on someone else's thread too |
| `--allow-incomplete` | post even though the last tweet has replies (the overlay asks instead) |
| `--save-json f` / `--from-json f` | dump or reuse the parsed thread; `-` reads stdin |
| `--doctor` | check every layer and print which one is broken (see below) |
| `--install-listener` | install the socket-activated agent for the browser shortcut, print its token |
| `--uninstall-listener` | remove that agent |

## Browser shortcut (no terminal)

One-time setup:

```bash
xthread2social --install-listener       # prints a token; installs the agent for your OS
```

On macOS that writes `~/Library/LaunchAgents/io.github.borgr.xthread2social.plist`; on Linux
`~/.config/systemd/user/xthread2social.socket` plus a template
`xthread2social@.service`, then `systemctl --user enable --now`. Either way the init system
holds `127.0.0.1:8765` and starts the handler *per connection*, with the connected socket as
its stdin/stdout — so nothing runs between publishes and there is nothing to restart after a
reboot. On a headless Linux box `systemctl --user` needs `loginctl enable-linger $USER` first.

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

The box at the top of that overlay adds **your own words** above the author's text on the
first post. The preview re-splits as you type, so the character counts and post boundaries
you see are the real ones — a long note pushes the original down into a further post rather
than being truncated. The read is cached for 30 minutes, which is what makes that instant
(and means a thread previewed a minute ago still publishes if X's endpoint goes down in
between). The posts themselves are not editable: rendering is derived from the thread, so an
edit would have to survive re-splitting, and this box covers what a note actually needs.

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
which launchd (or systemd) starts **on connection** and reaps afterwards — no daemon, nothing to restart
after a reboot. Requests must carry the token, so no other page can make you publish, and the
socket is bound to loopback only. `--uninstall-listener` removes the agent;
`--install-listener --rotate-token` issues a new token (and invalidates the old one).

An extension is required rather than a bookmarklet: x.com sends `default-src 'self'`, so
page-context requests to `127.0.0.1` are blocked. Tampermonkey's `GM_xmlhttpRequest` is exempt.

If the listener is ever down, Tampermonkey's menu → *Copy CLI command (fallback)* puts a
ready-to-run command on the clipboard for a terminal.

## Giving it to someone else

Everything is in this one repo: the Python runner and the userscript are two halves of the
same install, not two projects. Nothing here is account- or machine-specific — no credential,
no handle, no path — so a friend needs no fork and no edit:

1. macOS or Linux with Python 3.10+ (see [Platforms](#platforms) — Windows gets the CLI only,
   and the Linux listener is so far untested on real hardware).
2. The three blocks above, in order: **Install** (clone, venv, `pip install -e . regex`,
   symlink onto PATH), **Configure** (their own Bluesky app password and Mastodon access token
   with `read:accounts`, `write:statuses`, `write:media`), **Browser shortcut**
   (`--install-listener`, then install the userscript from the raw URL and paste the token it
   printed).
3. `xthread2social --doctor` first if anything looks wrong — it checks all five layers and is
   the answer to almost every "it stopped working".

The userscript is deliberately installed from the raw URL rather than from their clone, so
pushed fixes reach their browser on the next update check. Their token is theirs: it is
generated locally, lives in their own secret store, and is a capability for *their* listener only.

## When it breaks

Start here, always:

```bash
xthread2social --doctor
```

Five independent things can stop a publish — the env file, the stored secrets, the
socket-activated agent, X's syndication endpoint, and a stale copy of the userscript in the
browser — and from
inside Chrome they all surface as the same "listener not reachable". `--doctor` walks them in
order, prints a line per layer, tails the listener log, and logs in to both accounts without
posting. Fix the topmost `FAIL`; anything below it is usually a consequence.

`--doctor` logs in to both accounts, and Bluesky rate-limits session creation - it is a
diagnosis, not something to put in a loop or a cron job.

Two failures worth naming, because both cost an afternoon here:

- **`serve binary`** — a plist pointing at a path that no longer exists (a moved or rebuilt
  venv) loads cleanly and then resets every connection. `--install-listener` again, from the
  interpreter the package is actually installed in.
- **`userscript (github)`** — reloading an x.com page does *not* reload the script. Tampermonkey
  swaps in a new copy only on an `@version` bump, so reinstall over the top from the raw URL.
  Every overlay prints its own version (`… · v0.5.1`) precisely so a stale copy is visible.

Publishes through the browser are logged with timestamps to
`~/.local/share/xthread2social/serve.log`, trimmed at 512 KiB so it never needs attention.
The per-thread ledger sits beside it in `ledger.json`; deleting an entry makes that thread
publishable again, which is the only manual surgery this tool has.

The syndication endpoint is undocumented (it powers Vercel's `react-tweet`). If it stops
serving, `xthread2social --from-json` still accepts a thread from any source — that's the
fallback path, and the reason `thread.json` is the only interface between reading and posting.
`--doctor` fetches one ancient public tweet and checks the payload's *shape*, so a silent
change to the endpoint shows up as a `FAIL` line rather than as a mangled post.

```bash
python3 -m unittest discover -s tests      # 88 tests, offline except one secret-store round-trip
```
