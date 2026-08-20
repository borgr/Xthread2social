// ==UserScript==
// @name         Xthread2social — copy thread ends
// @namespace    https://github.com/borgr/Xthread2social
// @version      0.1.0
// @description  On an X thread, copy a ready-to-run xthread2social command for the thread you are looking at.
// @match        https://x.com/*/status/*
// @match        https://twitter.com/*/status/*
// @downloadURL  https://raw.githubusercontent.com/borgr/Xthread2social/main/userscript/xthread2social.user.js
// @updateURL    https://raw.githubusercontent.com/borgr/Xthread2social/main/userscript/xthread2social.user.js
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

// Deliberately dumb: this collects tweet IDs and nothing else. No thread parsing, no
// GraphQL, no credentials — the reader and writer live in Python where they are tested.
// Snowflake IDs are time-ordered, so min/max of the author's rendered tweets are the ends.
(function () {
  'use strict';

  const pageAuthor = () => location.pathname.split('/')[1];

  function collect() {
    const author = pageAuthor().toLowerCase();
    const ids = new Set();
    for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
      const m = a.getAttribute('href').match(/^\/([^/]+)\/status\/(\d+)/);
      if (m && m[1].toLowerCase() === author) ids.add(m[2]);
    }
    return [...ids].sort((x, y) => (BigInt(x) < BigInt(y) ? -1 : 1));
  }

  function command() {
    const ids = collect();
    if (!ids.length) return null;
    const first = `https://x.com/${pageAuthor()}/status/${ids[0]}`;
    const last = `https://x.com/${pageAuthor()}/status/${ids[ids.length - 1]}`;
    // Both ends when we saw more than one, so the CLI can *prove* the chain is complete.
    return {n: ids.length, cmd: ids.length > 1 ? `xthread2social ${last} ${first}`
                                              : `xthread2social ${last}`};
  }

  function toast(msg, bad) {
    const el = document.createElement('div');
    el.textContent = msg;
    el.style.cssText = `position:fixed;z-index:99999;bottom:24px;left:50%;transform:translateX(-50%);
      padding:10px 16px;border-radius:8px;font:14px/1.4 system-ui;color:#fff;max-width:70vw;
      background:${bad ? '#b3261e' : '#1d9bf0'};box-shadow:0 2px 12px rgba(0,0,0,.35)`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function run() {
    const got = command();
    if (!got) return toast('xthread2social: no tweets found — scroll the thread first', true);
    GM_setClipboard(got.cmd);
    // The count is informational only: the CLI re-walks the chain and refuses to post if
    // the tail may have a continuation. Scrolling to the end is still on you.
    toast(`copied (${got.n} tweets seen) — paste in a terminal`);
  }

  document.addEventListener('keydown', (e) => {
    if (e.shiftKey && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 't') {
      e.preventDefault();
      run();
    }
  });
  GM_registerMenuCommand('Copy xthread2social command', run);
})();
