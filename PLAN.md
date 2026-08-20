# thread2social — build plan (v2)

Point it at an X thread; it republishes the thread to **my own** Bluesky + Mastodon.
Supersedes `PLAN.v1.md` (userscript + GitHub Actions). Reason for the rewrite is at the end.

## Goals, ranked

The ranking is the design tool: when two options conflict, the higher goal wins.

1. **Faithful.** A thread in → the same thread out, in order, with images, clickable links,
   and long-post text intact. Nothing silently dropped.
2. **Still works in two years with no attention.** Fewest moving parts; every dependency
   either public-and-stable or trivially replaceable.
3. **Never half-publishes.** Verify completeness *before* posting; a partial failure is
   resumable without double-posting.
4. **$0 recurring**, no server, no paid API, no daemon.
5. **Cannot post as `colab-links`.** Separate repo, separate credentials, minimal scope.
6. **Reviewable before it goes public** (`--dry-run` prints exactly what will post).

Non-goals for v1: video, unattended/cron mirroring, posting *to* X, protected accounts,
a scheduling UI.

## Architecture: one contract, swappable readers

```
reader ──▶ thread.json ──▶ writer ──▶ Bluesky + Mastodon
                (the contract)
```

`thread.json` is the only interface: `{source_url, author, tweets:[{id, text, links[],
media[{url, alt}], quoted}]}`. Readers and writers are independently replaceable — this is
goal 2 expressed as structure, and it is why v2 has no browser and no CI in the required path.

### Reader A — syndication parent-walk (default; **verified end-to-end**)

`GET https://cdn.syndication.twimg.com/tweet-result?id=<id>&token=a` — no auth, no key,
no browser. Each tweet carries `in_reply_to_status_id_str`, so you walk a thread *backwards*
from its last tweet.

Validated 2026-08-19 on a real 8-tweet thread (`@nthngdy` 2090073045146677693 →
2090073048565072360): walked 8/8, reached the given root exactly, every tweet's full text
(up to 289 chars), 8 photo URLs across 7 tweets, and `entities.urls` expanded
`t.co` back to the real arxiv/HF links. Also confirmed: `quoted_tweet` inlines quotes,
`parent` carries the previous tweet as a fallback if the flat field ever disappears.

**Invocation takes both ends:** `thread2social <first_url> <last_url>`. That is not a
UX wart, it is goal 3 — walking back from the last tweet and asserting it reaches the
declared first tweet is a *deterministic* completeness proof. v1's "did I scroll far enough?"
count could not do this. Hard gates before anything posts: chain reaches the root; every
hop contiguous; single author throughout (foreign replies mean the wrong `last_url`).

### Reader B — userscript (only if A dies or the thread is gated)

Same `thread.json`, produced from the logged-in page and pasted in
(`thread2social post --from-clipboard`). Written **only when needed** — a protected thread
or a dead syndication endpoint. Keeping it out of v1 removes the userscript, the PAT, the
workflow, and the runner from the maintenance surface at once. `prinsss/twitter-web-exporter`
(MIT) is the interceptor to adopt if that day comes.

### Writer — local CLI

Runs where your Python and credentials already are. No GitHub Actions in v1: its only job was
keeping app passwords out of the browser, which stops being a problem once the browser is out
of the loop. Fine-grained PAT expiry (30 days default, 1 year max, no renewal) was the most
likely way v1 would quietly die.

If phone-triggering is ever wanted, it bolts on behind this same CLI: a workflow that runs
`thread2social <first> <last>`, dispatched by `curl`. Deferred, not designed out.

## Trigger: browser shortcut (Tampermonkey, deliberately dumb)

Yes to a userscript — but a different one than v1's. It collects **ids only**; it never parses
the thread, touches GraphQL, or holds a credential. ~30 lines, and the reader stays in Python
where it is tested.

What it does on `x.com/*/status/*`: scan rendered `article[data-testid="tweet"]` permalinks,
keep those by the page author, take min and max id (snowflake ids are time-ordered, so max is
the last tweet *rendered*), then hand off `first`/`last`. Bind it to a key (`Ctrl+Shift+T`)
via `document.addEventListener('keydown')` plus a `GM_registerMenuCommand` entry.

**Why an extension and not a bookmarklet:** X sets `default-src 'self'` with no `connect-src`,
so a page-context `fetch` to `127.0.0.1` is blocked outright — verified from the live CSP
header. Tampermonkey's `GM_xmlhttpRequest` is exempt from page CSP; nothing in the page is.
(Bookmarklet `javascript:` execution is also browser-dependent under CSP and has broken on X
before.) A userscript is the reliable vehicle either way.

**`first` is derivable, so the script only really needs `last`.** The walk terminates by itself
when `in_reply_to_status_id_str` is null or the author changes, so the declared root is a
*confirmation*, not a requirement — nice, because "last" is the end the browser can see
(you're scrolled there) and the CLI can't infer.

**Detecting a short capture without a declared root:** the last tweet's `conversation_count`.
On the verified thread it was `1` for a mid-thread tweet — i.e. it counts replies. So
`conversation_count > 0` on the tweet you called last means *there may be another self-reply
below*; warn and ask rather than post silently. Not proof (a stranger's reply counts too), but
it catches the didn't-scroll-to-the-end case, which is the one that half-publishes.

### Handoff, in shipping order

1. **Clipboard (v1.1, zero infra).** Script writes `thread2social <first> <last>` to the
   clipboard and toasts. You paste into a terminal. Always works, nothing to keep alive.
2. **Socket-activated localhost listener (v1.2, one keypress).** `GM_xmlhttpRequest` POSTs to
   `127.0.0.1:8765`; a launchd agent with a `Sockets` key starts the handler *on connection*
   and exits after — on-demand, no idle daemon, so goal 4 holds. You already run a launchd
   agent for the relay, so the pattern is known-good here. Bind to `localhost` only, require a
   shared token in the header so no other page can trigger a post.
3. **Custom URL scheme (alternative to 2).** `thread2social://post?last=…` via a small
   registered app bundle. Same one-keypress result, no listening socket, but a heavier and
   more macOS-specific one-time setup. Pick 2 unless the socket bothers you.

Publishing still runs `--dry-run` first by default: the shortcut collects and previews, and a
second confirm posts. The browser never becomes the thing that publishes.

## Reuse from `~/PycharmProjects/discord_atproto_bridge/relay.py`

Vendored into `social.py`, not imported — different accounts, and this tool must never hold
`colab-links` credentials (goal 5). Vendoring source is fine; sharing *secrets* is not.

| From relay.py | Use |
|---|---|
| `chunk_text` + `_url_reductions/_eff/_boundaries/_is_sentence_end` | sentence-aware splitting, already parameterized `limit`/`url_eff` (Bluesky 300, Mastodon 480, URLs=23) |
| `build_richtext` | Bluesky facets → clickable links |
| `_mastodon_request`, `_multipart`, `_mastodon_media`, `post_to_mastodon` | whole Mastodon path incl. media upload + 202-processing poll |
| `download(url, cap)` | media fetch with UA header |
| `test_chunking.py`, `test_mastodon.py` | starting suite |

A `Tweet→shim` adapter (`.content`, `.attachments[].url/.content_type/.filename/.description`)
reuses the Mastodon functions unchanged.

Genuinely new code: **Bluesky images** (`send_images`, atproto 0.0.69, accepts `reply_to`) with
Pillow resize under the 1 MB blob cap and ≤4 images per post; the reader; the completeness gates.

## Corrections carried over from the v1 critique

- **Per-target ledger.** `ledger.json` keyed `(root_id, target)`, not root alone — the common
  failure is Bluesky-ok/Mastodon-500, and a root-only key either double-posts or refuses to
  retry. This is the lesson `relay.py`'s `Target` table already learned; v1 had regressed it.
- **Grapheme counting.** Bluesky's 300 limit counts graphemes; `chunk_text` counts Python
  chars, so emoji ZWJ sequences mis-measure and the post is rejected *after* earlier posts are
  live. Use `regex`'s `\X` for the length function, keep a 280 safety margin.
- **Alt text is not in the syndication payload** (`ext_alt_text` absent on the verified
  thread). Images would post with empty alt — an accessibility regression by Bluesky/Mastodon
  norms. `--alt` prompts per image in interactive mode; `--no-alt` to skip deliberately.
- **Attribution.** Not your thread → `— via @handle` plus the source URL on the final post,
  and check your Mastodon instance's rules on mirroring before the first non-self thread.
- **Visibility.** Root public, replies unlisted on Mastodon (relay precedent, avoids flooding);
  `--all-public` to override.

## Final design pass (folded in before implementation)

1. **Accept 1..N tweet URLs, order-insensitive.** Snowflake ids are time-ordered, so the
   highest id is the tail and (if two or more are given) the lowest is the declared root.
   `thread2social <url>` works; adding the other end upgrades the completeness check to proof.
2. **Strip X's appended media link by matching `mediaDetails[].url`.** A tweet with photos has
   a trailing `t.co` pointing at the image, which must not survive into the post.
   `display_text_range` looked like the way to trim it and is a trap — its indices are UTF-16
   units over partly-unescaped text, so an emoji or an `&amp;` shifts them (caught in testing:
   it cut a real thread mid-URL). Matching the media `url` exactly has no index math at all.
   Remaining `t.co` links are swapped for `entities.urls` `expanded_url`, then HTML entities
   are unescaped.
3. **Two-phase publish per target: all media first, then all records.** Bluesky blobs and
   Mastodon media ids are invisible until a post references them, so uploading everything
   before creating the first record makes a resize/upload failure a no-op instead of a
   half-published thread. Directly serves goal 3.
4. **Ledger records progress *within* a thread**: `(root_id, target) → {posted: [i…],
   refs: […]}`. A failure at tweet 5 of 8 resumes at 5 with the correct parent ref rather than
   reposting or stalling.
5. **Preview is the default; publishing needs a second act.** `--post` publishes outright;
   without it you get the preview and, on a terminal, a `Publish this now? [y/N]` prompt — so
   one paste from the browser shortcut suffices, but the shortcut itself is never one keystroke
   from a live post. Piped/scripted runs stay preview-only (no TTY, no prompt).
6. **Config lives in `~/.config/thread2social/env`**, not `./.env` — the browser shortcut and
   launchd handler run from an arbitrary cwd.
7. **Grapheme length is optional-dependency, not required.** `regex`'s `\X` when importable,
   otherwise char count with a wider margin. Correct where it matters, still installs bare.
8. **A test guards vendor drift**: it compares the vendored `chunk_text` against
   `discord_atproto_bridge/relay.py` when that path exists, and skips when it doesn't.

## Build order

1. `tests/fixtures/nthngdy.json` — save the walked thread above; everything downstream is
   testable offline, no network.
2. `read_syndication.py` + tests: walk, the three gates, `note_tweet`, media, `t.co` expansion,
   quoted tweets, foreign-reply rejection.
3. `social.py`: vendor from relay.py, add the Bluesky image path, grapheme length. `--dry-run`
   against the fixture.
4. Credentials: Bluesky app password (personal handle) + Mastodon token scoped
   `write:statuses`+`write:media`, in a gitignored `.env`.
5. Live: one single-tweet thread to both accounts, then the 8-tweet fixture thread for real.
6. Userscript + clipboard handoff; then the launchd socket listener.
7. Ledger + `README.md` (setup, the two-URL invocation, what to do when the reader breaks).

## Residual risks (accepted, with the response)

- **Syndication endpoint is undocumented** and could go away. It is a public embed API
  (powers Vercel's `react-tweet`), far more stable than X's private GraphQL, but not promised.
  Response: write Reader B. The contract means nothing else changes.
- **Media URLs** (`pbs.twimg.com`) are public today; if they ever require auth, images need
  Reader B.
- **`atproto` is pre-1.0** — pin it, don't float.
- **X ToS** discourages automated reading; this is manual, one thread at a time, from your own
  session. Volume stays human.
- **Video** out of scope; a video tweet posts its text plus the source link, with a warning.

## Why v1 was replaced

v1 conflated two independent choices — *how to read* and *where to run* — and picked the
harder option on both. Reading via X's private GraphQL from a logged-in browser is the most
capable path but rots on X's schedule; the Actions hop added a PAT, two secret stores, a
runner, an unpinned `pip install`, and a ledger commit, all to solve a credential problem that
disappears when the browser leaves. The parent-walk verification above made the capable-but-
fragile reader unnecessary for public threads, which is every thread in scope for v1.
