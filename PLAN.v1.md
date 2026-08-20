# Xthread2social — build plan

Point at an X/Twitter thread in the browser, press one button, and it posts to **your own**
Bluesky + Mastodon accounts. Separate repo, separate credentials — no sharing with
`ATProto-links-bot` / colab-links.

## Architecture (two components, one HTTP call between them)

```
x.com thread  ──[userscript reads X's own GraphQL response]──►  thread JSON
                                                                    │
                                        GM_xmlhttpRequest POST      │  (PAT: Actions:write, 1 repo)
                                                                    ▼
                                  GitHub  repository_dispatch ──► Actions runner
                                                                    │  (secrets: bsky + mastodon)
                                                            ┌───────┴───────┐
                                                         Bluesky        Mastodon
```

Credential split is the point: the browser holds only a revocable, single-repo,
`Actions: write` fine-grained PAT. The social write-credentials live as GitHub Secrets and
never touch x.com.

## Component A — `xthread2social.user.js` (Tampermonkey)

`@match https://x.com/*/status/*`, `@connect api.github.com`, `@grant GM_xmlhttpRequest`,
`GM_setValue`, `GM_setClipboard`.

**Read via network interception, not DOM scraping.** Hook `XMLHttpRequest.prototype.open/send`
and `window.fetch`; keep responses whose URL contains `/graphql/` and `TweetDetail`. That is
X's own structured payload for the thread — full text, expanded URLs, media, reply parentage,
in display order. Beats `document.querySelector` on obfuscated class names, and beats the
`cdn.syndication.twimg.com` endpoint (which serves one tweet at a time and can't walk a
thread forward).

Parse path:
`data.threaded_conversation_with_injections_v2.instructions[] → TimelineAddEntries →
entries[] → content.itemContent.tweet_results.result` plus `content.items[]` for
`conversationthread-*` modules.

Per tweet, extract:
- `rest_id`
- `note_tweet.note_tweet_results.result.text` if present (long posts), else `legacy.full_text`
- `legacy.entities.urls[]` → substitute each `t.co` with `expanded_url`
- `legacy.extended_entities.media[]` → `media_url_https`, `type`, `ext_alt_text`
- `legacy.in_reply_to_status_id_str` → order/verify the chain
- quoted tweet → its permalink, appended as a link

Keep only tweets authored by the root author (self-thread); drop replies from others. Strip
the trailing `t.co` self-link X appends for media.

**UI:** floating button showing the captured count (`▶ 7 tweets → post`). The count is the
honest signal that you must scroll to the end of the thread first — X's timeline is
virtualized and only loads what you've seen. Clicking sends; shift-click copies the JSON to
the clipboard instead (offline path, and the debug path).

**Config** in `GM_setValue`: `gh_pat`, `gh_repo`, `dry_run`. Small options panel.

**Send:** `POST https://api.github.com/repos/<owner>/<repo>/dispatches` with
`{"event_type":"thread","client_payload":{"payload":"<json string>","dry_run":false}}`.
`client_payload` allows at most 10 top-level properties, so the whole thread rides as one
JSON string. Media travel as **URLs**, not bytes — `pbs.twimg.com` is public and the runner
fetches them itself, which keeps the dispatch tiny. Response is `204` with no run id, so on
success the script toasts a link to the repo's Actions tab.

## Component B — private repo `xthread2social`

```
post.py                     # the poster
social.py                   # vendored posting helpers (see reuse below)
tests/
  fixtures/tweetdetail_*.json
  test_parse.py test_chunk.py
posted.json                 # ledger, committed back by the workflow
.github/workflows/post.yml  # on: repository_dispatch [thread]  (+ workflow_dispatch for manual)
```

Workflow: checkout → `pip install atproto pillow` → run `post.py` with the payload passed
through an **env var** (never interpolated into a shell line — that's a script-injection
hole) → `git commit posted.json` if changed.

`post.py`:
1. Parse payload; bail if `root_id` is already in `posted.json` (double-click safety).
2. Build post text per tweet; `chunk_text` splits anything over the per-network limit.
3. Bluesky: `client.send_images(text, images=[bytes], image_alts=[...], reply_to=ref)`
   chained root→parent for the thread. Blob cap is ~976 KB and 4 images per post, so
   Pillow-resize before upload.
4. Mastodon: `POST /api/v2/media` (await the 202→processed poll), then
   `POST /api/v1/statuses` chained via `in_reply_to_id`. Root `public`, replies `unlisted`.
5. Attribution: when the thread isn't yours, append `— via @handle` + the source URL.
6. `dry_run` prints the exact posts and uploads nothing.

Secrets: `BSKY_HANDLE`, `BSKY_APP_PASSWORD`, `MASTODON_BASE_URL`, `MASTODON_ACCESS_TOKEN`.
Private repo → 2,000 free Actions minutes/month, ~1 min per run.

## Code to reuse (verified present, `~/PycharmProjects/discord_atproto_bridge/relay.py`)

Copy into `social.py` — vendored, not imported: different repo, different accounts, and this
tool must not be able to post as colab-links.

| From relay.py | Use |
|---|---|
| `chunk_text` + `_url_reductions/_eff/_boundaries/_is_sentence_end` | sentence-aware splitting, already parameterized `limit` + `url_eff` (Bluesky 300 / Mastodon 480, URLs=23) |
| `build_richtext` | Bluesky facets so links are clickable |
| `_mastodon_request`, `_multipart`, `_mastodon_media`, `post_to_mastodon` | complete Mastodon path incl. image upload and 202-processing poll |
| `download(url, cap)` | fetch media bytes with a UA header |
| `test_chunking.py`, `test_mastodon.py` | starting test suite |

`_mastodon_media` and `post_to_mastodon` take a duck-typed `message` with `.content` and
`.attachments[].url/.content_type/.filename/.description` — a small `Tweet→shim` adapter
reuses them unchanged.

**Gap:** `post_to_bluesky` only builds link-card embeds (`make_embed`) — it has no image
path. `send_images` (confirmed in atproto 0.0.69, accepts `reply_to`) plus Pillow resize is
genuinely new code.

## Existing projects surveyed

- **prinsss/twitter-web-exporter** (MIT, active, 2.6k★) — userscript that captures X's
  GraphQL responses via a network interceptor. Its interceptor is the piece worth adopting
  (license permits). It does *not* handle single-thread/`TweetDetail` export — its surfaces
  are profile/bookmarks/search — so the parser is ours.
- **louisgrasset/touitomamout** (AGPL-3.0, **archived Aug 2025**) — TS Twitter→Bluesky+Mastodon
  crossposter over `@the-convocation/twitter-scraper`. Closest prior art, but it mirrors your
  own timeline on a cron and needs Twitter creds. Reference for thread/media mapping only;
  AGPL, so read it, don't copy it.
- **`cdn.syndication.twimg.com/tweet-result`** — no-auth single-tweet JSON (tested working:
  full text via `note_tweet`, `entities.urls`, `mediaDetails`, `quoted_tweet`). Keep as a
  **fallback enricher** for a tweet the interceptor missed. Its sibling
  `timeline/conversation` is dead (200, empty body).
- **postiz** (self-hosted multi-network scheduler) — a whole app with a DB; overkill here.
- X official API — free tier has no usable read quota, and a link-bearing post costs $0.20
  to write. Not used in either direction.

## Build order

1. Capture one real `TweetDetail` response from a live thread → `tests/fixtures/`. Everything
   downstream is then testable offline.
2. `parse.py` + `test_parse.py` against the fixture (order, long text, media, quotes, foreign
   replies dropped).
3. `social.py`: vendor from relay.py, add the Bluesky image path. Dry-run against the fixture.
4. Live single-tweet test to both accounts, then a real multi-tweet thread.
5. Workflow + secrets + PAT; dispatch by `curl` first, userscript second.
6. Userscript: interceptor → parser → button → dispatch.
7. Ledger, alerting (Actions failure email is enough), README with the setup steps.

## Known risks

- **X changes the GraphQL shape** → parser breaks. Failure is loud (count stays 0). Mitigated
  by matching on the `TweetDetail` substring rather than the endpoint's hash, and by tolerant
  field lookups with the syndication fallback.
- **Virtualized timeline** → short capture if you don't scroll to the end. The visible count
  is the guard.
- **PAT in the browser** → can only trigger this one repo's workflows. Rotate freely.
- **Protected-account media** isn't fetchable by the runner → those threads post text-only,
  with a warning.
- **Video** is out of scope for v1 (Bluesky video has its own limits and a separate upload
  path). Images only; video becomes a link.
