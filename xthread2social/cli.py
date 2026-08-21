"""xthread2social — republish an X thread to your own Bluesky and Mastodon.

    xthread2social <last-tweet-url> [<first-tweet-url>]      # preview (default)
    xthread2social <urls...> --post                          # publish
"""
import argparse
import json
import pathlib
import sys

from . import config
from .ledger import Busy, Ledger, lock
from .model import Thread
from .read_syndication import IncompleteError, ReadError, read_thread
from .targets import Bluesky, Mastodon, PostError, render


def build_targets(args):
    """Enabled targets, in publish order. A target with no credentials is skipped
    rather than fatal, so you can run with only one configured."""
    out = []
    if "bluesky" in args.to:
        handle, pw = config.get("ATPROTO_HANDLE"), config.get("ATPROTO_APP_PASSWORD")
        if handle and pw:
            out.append(Bluesky(handle, pw, config.get("ATPROTO_PDS", "https://bsky.social")))
        else:
            print("[skip] bluesky: set ATPROTO_HANDLE + ATPROTO_APP_PASSWORD")
    if "mastodon" in args.to:
        base, tok = config.get("MASTODON_BASE_URL"), config.get("MASTODON_ACCESS_TOKEN")
        if base and tok:
            out.append(Mastodon(base, tok, all_public=args.all_public))
        else:
            print("[skip] mastodon: set MASTODON_BASE_URL + MASTODON_ACCESS_TOKEN")
    return out


def check_credentials(args):
    """Verify each configured target's credentials without posting anything."""
    print(f"config: {config.PATH} ({'found' if config.PATH.exists() else 'MISSING'})")
    targets = build_targets(args)
    if not targets:
        return 2
    bad = 0
    for tgt in targets:
        try:
            tgt.login()
            who = getattr(tgt, "username", None) or getattr(tgt, "handle", "?")
            print(f"[ok]   {tgt.name}: authenticated as {who}")
        except Exception as e:
            print(f"[FAIL] {tgt.name}: {e}")
            bad += 1
    return 1 if bad else 0


def _my_handles():
    """Every X handle that counts as you.

    X_HANDLES in the env file is the reliable answer (X, Bluesky and Mastodon names need not
    agree); the Bluesky handle's first label is a fallback for the common case where they do.
    """
    names = {h.strip().lstrip("@").lower()
             for h in config.get("X_HANDLES", "").split(",") if h.strip()}
    if names:
        return names
    fallback = config.get("ATPROTO_HANDLE", "").split(".")[0].lower()
    return {fallback} if fallback else set()


def _is_mine(thread):
    return thread.author.lower() in _my_handles()


def attribution_for(thread, args):
    """Closing line: the source link, plus who wrote it when it isn't you.

    Your own thread gets neither - reposting yourself is just posting, and a link back to X
    on your own words reads as advertising X rather than crediting anyone. `--source-link`
    adds the bare URL back for the times you do want to point at the original.
    """
    if args.no_attribution:
        return ""
    if _is_mine(thread):
        return f"\n\n{thread.source_url}" if getattr(args, "source_link", False) else ""
    return f"\n\n— x-post from @{thread.author} {thread.source_url}"


def credit_for(thread, args):
    """Opening credit for the first post: wordings longest-first, never for your own thread.

    render() picks the longest one that still fits without splitting the opening tweet, and
    falls back to the shortest - the credit is always on the first post either way.
    """
    if args.no_attribution or _is_mine(thread):
        return ""
    h = thread.author
    return [f"\n\n\U0001F501 x-post from @{h}",
            f"\n\n\U0001F501 x-post @{h}",
            f"\n\n\U0001F501 @{h}"]


def ask_alt(thread):
    """Prompt for alt text per image (X's syndication payload has none)."""
    for t in thread.tweets:
        for m in t.media:
            if m.kind == "photo" and not m.alt:
                try:
                    m.alt = input(f"  alt for {m.url.rsplit('/', 1)[-1]} (blank to skip): ").strip()
                except EOFError:
                    return


def preview(thread, args):
    print(f"\n{thread.author}: {len(thread.tweets)} tweets, "
          f"{sum(len(t.media) for t in thread.tweets)} images -> {thread.source_url}")
    for w in thread.warnings:
        print(f"  [warn] {w}")
    for kind in args.to:
        units = render(thread, kind, attribution_for(thread, args), credit_for(thread, args),
                       note=getattr(args, "note", "") or "")
        print(f"\n--- {kind}: {len(units)} post(s) ---")
        for i, u in enumerate(units, 1):
            imgs = f"  [{len(u['images'])} image(s)]" if u["images"] else ""
            print(f"{i:>2}. ({len(u['text'])} chars){imgs}\n{u['text']}\n")


def publish(thread, args, targets):
    with lock(thread.root_id):
        return _publish_locked(thread, args, targets)


def _publish_locked(thread, args, targets):
    ledger = Ledger()
    failures = []
    for tgt in targets:
        units = render(thread, tgt.name, attribution_for(thread, args),
                       credit_for(thread, args), note=getattr(args, "note", "") or "")
        start, refs = ledger.done(thread.root_id, tgt.name)
        if start >= len(units):
            print(f"[{tgt.name}] already complete ({start} posts) - nothing to do")
            continue
        if start:
            print(f"[{tgt.name}] resuming at post {start + 1}/{len(units)}")
        try:
            tgt.login()
            tgt.prepare(units[start:])            # phase 1: nothing is public yet
        except Exception as e:
            print(f"[{tgt.name}] FAILED before posting (nothing published): {e}")
            failures.append(tgt.name)
            continue
        try:
            for i, refs_now in tgt.publish(units, start=start, refs=refs):
                ledger.record(thread.root_id, tgt.name, i + 1, refs_now, thread.source_url)
                print(f"[{tgt.name}] posted {i + 1}/{len(units)}")
        except Exception as e:
            print(f"[{tgt.name}] FAILED mid-thread: {e}\n"
                  f"           re-run the same command to resume where it stopped")
            failures.append(tgt.name)
            continue
        _, refs = ledger.done(thread.root_id, tgt.name)
        print(f"[{tgt.name}] done -> {refs[0][0] if refs else '?'}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xthread2social", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*", help="tweet URLs or ids; the newest is treated as "
                                           "the thread's last tweet, a second one as its first")
    ap.add_argument("--set-secret", choices=["bluesky", "mastodon"], metavar="TARGET",
                    help="prompt for a credential (not echoed) and store it in the Keychain")
    ap.add_argument("--check", action="store_true",
                    help="verify credentials for the selected targets and exit")
    ap.add_argument("--doctor", action="store_true",
                    help="check every layer (config, keychain, launchd agent, X's endpoint, "
                         "userscript version, credentials) and say which one is broken")
    ap.add_argument("--post", action="store_true", help="actually publish (default: preview)")
    ap.add_argument("--to", default="bluesky,mastodon", help="targets (default both)")
    ap.add_argument("--from-json", metavar="PATH", help="read a Thread from JSON ('-' for stdin) "
                                                       "instead of the syndication endpoint")
    ap.add_argument("--save-json", metavar="PATH", help="write the read thread as JSON")
    ap.add_argument("--alt", action="store_true", help="prompt for alt text per image")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="post even though the last tweet may have a continuation")
    ap.add_argument("--all-public", action="store_true",
                    help="Mastodon: post replies public too (default: unlisted)")
    ap.add_argument("--note", default="", metavar="TEXT",
                    help="your own words, prepended to the first post above the author's text")
    ap.add_argument("--no-attribution", action="store_true")
    ap.add_argument("--source-link", action="store_true",
                    help="on your own thread, still link back to the tweet (default: no "
                         "credit line at all)")
    ap.add_argument("--install-listener", action="store_true",
                    help="install the launchd agent that lets the browser shortcut publish "
                         "without a terminal, and print the token to paste into Tampermonkey")
    ap.add_argument("--rotate-token", action="store_true",
                    help="with --install-listener: issue a new token (invalidates the old one)")
    ap.add_argument("--uninstall-listener", action="store_true")
    args = ap.parse_args(argv)
    args.to = [t.strip() for t in args.to.split(",") if t.strip()]
    config.load()

    if args.set_secret:
        name = ("ATPROTO_APP_PASSWORD" if args.set_secret == "bluesky"
                else "MASTODON_ACCESS_TOKEN")
        try:
            config.prompt_secret(name)
        except (ValueError, RuntimeError) as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2
        print(f"[saved] {name} -> Keychain (service 'xthread2social')")
        args.to = [args.set_secret]
        return check_credentials(args)

    if args.install_listener or args.uninstall_listener:
        from . import listener
        if args.uninstall_listener:
            listener.uninstall()
            print("[removed] launchd agent; the browser shortcut falls back to the clipboard")
            return 0
        try:
            token = listener.install(rotate=args.rotate_token)
        except (RuntimeError, ValueError) as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2
        loaded, _ = listener.status()
        print(f"[ok] listening on 127.0.0.1:{listener.PORT} "
              f"({'loaded' if loaded else 'NOT loaded - check launchctl'})\n"
              f"     agent: {listener.PLIST}\n\n"
              f"Paste this token into the userscript once (Tampermonkey menu ->\n"
              f'"Set publish token" on an x.com tab):\n\n    {token}\n')
        return 0

    if args.doctor:
        from . import doctor
        return doctor.run(check_credentials, args)

    if args.check:
        return check_credentials(args)

    try:
        if args.from_json:
            if args.from_json == "-":
                blob = sys.stdin.read()
            else:
                blob = pathlib.Path(args.from_json).read_text()
            thread = Thread.from_json(blob)
        else:
            if not args.urls:
                ap.error("give at least one tweet URL, or --from-json")
            thread = read_thread(args.urls, allow_incomplete=args.allow_incomplete)
    except IncompleteError as e:
        print(f"[read] {e}\n       pass the real last tweet's URL, or re-run with "
              f"--allow-incomplete", file=sys.stderr)
        return 2
    except ReadError as e:
        print(f"[read] {e}", file=sys.stderr)
        return 2

    if args.save_json:
        with open(args.save_json, "w") as fh:
            fh.write(thread.to_json())
        print(f"[saved] {args.save_json}")
    if args.alt:
        ask_alt(thread)

    if not args.post:
        preview(thread, args)
        # One paste from the browser shortcut should be enough: offer to publish right
        # here when a human is watching, while a piped/scripted run stays preview-only.
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                if input("Publish this now? [y/N] ").strip().lower() in ("y", "yes"):
                    args.post = True
            except (EOFError, KeyboardInterrupt):
                print()
        if not args.post:
            print("Preview only. Re-run with --post to publish.")
            return 0

    targets = build_targets(args)
    if not targets:
        print("[error] no target configured - see ~/.config/xthread2social/env", file=sys.stderr)
        return 2
    try:
        return 1 if publish(thread, args, targets) else 0
    except Busy as e:
        print(f"[busy] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
