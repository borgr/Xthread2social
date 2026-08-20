// ==UserScript==
// @name         Xthread2social — publish thread
// @namespace    https://github.com/borgr/Xthread2social
// @version      0.5.0
// @description  Preview an X thread and publish it to your own Bluesky + Mastodon without leaving the browser.
// @match        https://x.com/*/status/*
// @match        https://twitter.com/*/status/*
// @downloadURL  https://raw.githubusercontent.com/borgr/Xthread2social/main/userscript/xthread2social.user.js
// @updateURL    https://raw.githubusercontent.com/borgr/Xthread2social/main/userscript/xthread2social.user.js
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

// This script never parses the thread and never holds an account credential: it collects
// tweet IDs, then asks the local listener (launchd, 127.0.0.1 only) to render a preview and
// - after you click Publish - to post it. The token is a capability for that listener,
// stored per-browser via GM_setValue, so this public file contains no secret.
//
// GM_xmlhttpRequest is required rather than fetch: x.com sends `default-src 'self'`, which
// blocks any page-context request to 127.0.0.1.
(function () {
  'use strict';

  const ENDPOINT = 'http://127.0.0.1:8765';

  // The hotkey is stored, not hardcoded: browser- and extension-level shortcuts (Chrome's
  // Cmd+Shift+T, 1Password's Ctrl+Shift+X) are consumed before the page ever sees a keydown,
  // so the only reliable fix for a clash is to pick a different combination - or to use the
  // floating button, which no shortcut can shadow. Matched on e.code, because Option+letter
  // on macOS reports a dead-key character in e.key.
  const DEFAULT_HOTKEY = {code: 'KeyX', alt: true, shift: true, ctrl: false, meta: false};
  const hotkey = () => Object.assign({}, DEFAULT_HOTKEY, GM_getValue('hotkey', null) || {});

  function matches(e, hk) {
    return e.code === hk.code && e.altKey === !!hk.alt && e.shiftKey === !!hk.shift &&
           e.ctrlKey === !!hk.ctrl && e.metaKey === !!hk.meta;
  }

  function hotkeyLabel(hk) {
    return [hk.ctrl && 'Ctrl', hk.alt && 'Option', hk.shift && 'Shift', hk.meta && 'Cmd',
            hk.code.replace(/^(Key|Digit)/, '')].filter(Boolean).join('+');
  }

  const pageAuthor = () => location.pathname.split('/')[1];
  const token = () => GM_getValue('token', '');

  function collectIds() {
    const author = pageAuthor().toLowerCase();
    const ids = new Set();
    for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
      const m = a.getAttribute('href').match(/^\/([^/]+)\/status\/(\d+)/);
      if (m && m[1].toLowerCase() === author) ids.add(m[2]);
    }
    const m = location.pathname.match(/\/status\/(\d+)/);
    if (m) ids.add(m[1]);
    return [...ids].sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : 1));
  }

  function urlsForRequest() {
    const ids = collectIds();
    if (!ids.length) return null;
    const u = id => `https://x.com/${pageAuthor()}/status/${id}`;
    // Both ends when we saw more than one, so the CLI can *prove* the chain is complete.
    return ids.length > 1 ? [u(ids[ids.length - 1]), u(ids[0])] : [u(ids[0])];
  }

  function call(path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: path === '/ping' ? 'GET' : 'POST', url: ENDPOINT + path, timeout: 600000,
        headers: {'Content-Type': 'application/json', 'X-Token': token()},
        data: JSON.stringify(payload),
        onload: r => {
          let body = {};
          try { body = JSON.parse(r.responseText); } catch (e) { /* keep the raw status */ }
          if (r.status === 200 && !body.error) resolve(body);
          else reject(new Error(body.error || `listener returned HTTP ${r.status}`));
        },
        onerror: () => reject(new Error('listener not reachable - run `xthread2social --install-listener`')),
        ontimeout: () => reject(new Error('listener timed out')),
      });
    });
  }

  // ---------- overlay ----------

  const css = `
    #x2s-wrap{position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.6);
      display:flex;align-items:center;justify-content:center;font:14px/1.45 -apple-system,sans-serif}
    #x2s{background:#16181c;color:#e7e9ea;max-width:620px;width:92%;max-height:86vh;overflow:auto;
      border-radius:14px;padding:18px 20px;box-shadow:0 8px 40px rgba(0,0,0,.5)}
    #x2s h2{margin:0 0 4px;font-size:16px}
    #x2s .sub{color:#8b98a5;margin-bottom:12px}
    #x2s .warn{background:#3a2a10;border-left:3px solid #d9a13a;padding:6px 9px;margin:6px 0;border-radius:4px}
    #x2s .post{border:1px solid #2f3336;border-radius:9px;padding:8px 10px;margin:7px 0;white-space:pre-wrap}
    #x2s .meta{color:#8b98a5;font-size:12px;margin-bottom:4px}
    #x2s .row{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
    #x2s button{border:0;border-radius:999px;padding:9px 16px;font-weight:600;cursor:pointer}
    #x2s .go{background:#1d9bf0;color:#fff}
    #x2s .cancel{background:#2f3336;color:#e7e9ea}
    #x2s a{color:#1d9bf0}
    #x2s label{color:#e7e9ea;display:flex;gap:5px;align-items:center}`;

  function close() {
    const w = document.getElementById('x2s-wrap');
    if (w) w.remove();
  }

  function shell(title, sub) {
    close();
    const wrap = document.createElement('div');
    wrap.id = 'x2s-wrap';
    wrap.innerHTML = `<style>${css}</style><div id="x2s"><h2></h2><div class="sub"></div>
      <div class="body"></div></div>`;
    wrap.querySelector('h2').textContent = title;
    wrap.querySelector('.sub').textContent = sub || '';
    wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
    document.body.appendChild(wrap);
    return wrap.querySelector('.body');
  }

  const esc = s => String(s).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));

  function showPreview(urls, data) {
    const kinds = Object.keys(data.targets);
    const shown = kinds[0];
    const n = data.tweets;
    // Say "N tweets -> M posts" explicitly: a tweet past the target's character cap becomes
    // several posts, and without this line a 1-tweet thread showing "2 posts" reads as the
    // reader having swept up a stray tweet.
    const sub = kinds.map(k => {
      const m = data.targets[k].length;
      return `${k}: ${m} post${m === 1 ? '' : 's'}` + (m > n ? ' (split to fit)' : '');
    }).join('   ·   ');
    const body = shell(`@${data.author} - ${n} tweet${n === 1 ? '' : 's'}`, sub);
    let html = data.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join('');
    html += data.targets[shown].map((p, i) => {
      const bits = [`${i + 1}.`];
      if (p.parts > 1) bits.push(`part ${p.part}/${p.parts} of tweet ${p.tweet}`);
      bits.push(`${p.text.length} chars`);
      if (p.images.length) bits.push(`${p.images.length} image(s)`);
      if (kinds.length > 1 && i === 0) bits.push(`showing ${shown}`);
      return `<div class="post"><div class="meta">${esc(bits.join(' · '))}</div>` +
             `${esc(p.text)}</div>`;
    }).join('');
    html += '<div class="row">' +
      kinds.map(k => `<label><input type="checkbox" class="t" value="${k}" checked>${k}</label>`).join('') +
      '<span style="flex:1"></span><button class="cancel">Cancel</button>' +
      '<button class="go">Publish</button></div>';
    body.innerHTML = html;
    body.querySelector('.cancel').onclick = close;
    body.querySelector('.go').onclick = () => {
      const to = [...body.querySelectorAll('.t:checked')].map(c => c.value);
      if (to.length) doPublish(urls, to);
    };
  }

  function showResult(data) {
    const body = shell('Published', `@${data.author}`);
    body.innerHTML = Object.entries(data.results).map(([k, v]) =>
      `<div class="post">${k}: ${v.failed ? 'FAILED - see the log' : `${v.posts} posts`}` +
      `${v.url ? ` - <a href="${esc(v.url)}" target="_blank" rel="noopener">open</a>` : ''}</div>`).join('') +
      '<div class="row"><span style="flex:1"></span><button class="cancel">Close</button></div>';
    body.querySelector('.cancel').onclick = close;
  }

  function showError(e) {
    const body = shell('Xthread2social', '');
    body.innerHTML = `<div class="warn">${esc(e.message || e)}</div>
      <div class="row"><button class="cancel">Close</button></div>`;
    body.querySelector('.cancel').onclick = close;
  }

  async function doPublish(urls, to) {
    shell('Xthread2social', 'publishing - uploading images, then posting…');
    try {
      showResult(await call('/publish', {urls, to, allow_incomplete: true}));
    } catch (e) { showError(e); }
  }

  // One screen, not two. The reader's tail gate (the last tweet has replies, one of which
  // might be the author's own continuation) used to open its own confirm dialog before the
  // preview - but the preview already lists that warning and already requires a click on
  // Publish, so the extra dialog asked the same question twice. The overlay therefore reads
  // with allow_incomplete on and lets the warning + Publish button be the confirmation.
  // The CLI keeps the hard gate: there, nothing is on screen to overrule it.
  async function preview(urls) {
    shell('Xthread2social',
          `reading the thread (${urls.length > 1 ? 'both ends given' : 'walking back from this tweet'})…`);
    try {
      showPreview(urls, await call('/preview', {urls, allow_incomplete: true}));
    } catch (e) { showError(e); }
  }

  function run() {
    if (!token()) return askToken(true);
    const urls = urlsForRequest();
    if (!urls) return showError(new Error('no tweets found on this page - reload and retry'));
    preview(urls);
  }

  function askToken(thenRun) {
    const v = prompt('Paste the token printed by `xthread2social --install-listener`:', token());
    if (v === null) return;
    GM_setValue('token', v.trim());
    if (thenRun) run();
  }

  function copyCommand() {
    const urls = urlsForRequest();
    if (urls) GM_setClipboard('xthread2social ' + urls.join(' '));
  }

  // ---------- triggers ----------

  let capturing = null;                 // set while "Change hotkey" waits for a keypress

  function onKeydown(e) {
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA)$/.test(t.tagName))) return;
    if (capturing) {
      if (e.key === 'Escape') { capturing = null; return finishCapture(null); }
      if (/^(Alt|Shift|Control|Meta)$/.test(e.key)) return;   // wait for a real key
      e.preventDefault();
      e.stopPropagation();
      const hk = {code: e.code, alt: e.altKey, shift: e.shiftKey,
                  ctrl: e.ctrlKey, meta: e.metaKey};
      capturing = null;
      GM_setValue('hotkey', hk);
      return finishCapture(hk);
    }
    if (!matches(e, hotkey())) return;
    e.preventDefault();
    e.stopPropagation();
    run();
  }

  // Both document and window, capture phase: X stops propagation on some keys, and a
  // listener that only sees the bubble phase silently never fires.
  document.addEventListener('keydown', onKeydown, true);
  window.addEventListener('keydown', onKeydown, true);

  function finishCapture(hk) {
    const body = shell('Xthread2social', hk ? 'hotkey saved' : 'unchanged');
    body.innerHTML = `<div class="post">${hk ? esc(hotkeyLabel(hk)) : 'cancelled'}</div>
      <div class="row"><span style="flex:1"></span><button class="cancel">Close</button></div>`;
    body.querySelector('.cancel').onclick = close;
  }

  function changeHotkey() {
    capturing = true;
    const body = shell('Press the new shortcut',
                       'anything the browser or another extension already owns will never ' +
                       'reach this page - if a combination does nothing, it is taken');
    body.innerHTML = `<div class="post">current: ${esc(hotkeyLabel(hotkey()))}
      \n(Esc cancels)</div>`;
  }

  // A button, because a hotkey can always be stolen by the browser or another extension.
  function addButton() {
    if (GM_getValue('hideButton', false) || document.getElementById('x2s-btn')) return;
    const b = document.createElement('button');
    b.id = 'x2s-btn';
    b.textContent = '\u{1F501} publish';
    b.title = `Xthread2social (${hotkeyLabel(hotkey())})`;
    b.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483646;' +
      'background:#1d9bf0;color:#fff;border:0;border-radius:999px;padding:10px 16px;' +
      'font:600 13px -apple-system,sans-serif;cursor:pointer;box-shadow:0 3px 14px rgba(0,0,0,.4)';
    b.onclick = run;
    document.body.appendChild(b);
  }
  addButton();
  new MutationObserver(addButton).observe(document.body, {childList: true});

  async function selfTest() {
    const ids = collectIds();
    let listener = 'not reachable';
    try {
      await call('/ping', {});
      listener = 'reachable, token accepted';
    } catch (e) {
      listener = String(e.message || e);
    }
    const body = shell('Xthread2social self-test', `script v0.5.0 running on ${location.host}`);
    body.innerHTML = [`hotkey: ${hotkeyLabel(hotkey())}`,
                      `tweets found on this page: ${ids.length}`,
                      `token stored: ${token() ? 'yes' : 'no'}`,
                      `listener: ${listener}`]
      .map(l => `<div class="post">${esc(l)}</div>`).join('') +
      '<div class="row"><span style="flex:1"></span><button class="cancel">Close</button></div>';
    body.querySelector('.cancel').onclick = close;
  }

  GM_registerMenuCommand('Publish this thread…', run);
  GM_registerMenuCommand('Change hotkey', changeHotkey);
  GM_registerMenuCommand('Set publish token', () => askToken(false));
  GM_registerMenuCommand('Self-test (is it loaded?)', selfTest);
  GM_registerMenuCommand('Hide/show the publish button', () => {
    GM_setValue('hideButton', !GM_getValue('hideButton', false));
    const b = document.getElementById('x2s-btn');
    if (b) b.remove();
    addButton();
  });
  GM_registerMenuCommand('Copy CLI command (fallback)', copyCommand);
})();
