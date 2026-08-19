/* lem.js — shared client helpers.
 *
 * Every page is a full document, so moving between Map / Checklists / PM&CAL
 * meant re-fetching everything and staring at "Loading…" each time — even
 * though LabCore had just been asked. This keeps the last good answer in
 * sessionStorage and paints it immediately, then refreshes in the background and
 * repaints if anything changed.
 *
 * Deliberately NOT declaring $ / esc here: the pages already declare those with
 * `const` at top level, and a second top-level `const` of the same name in
 * another classic script is a SyntaxError that would break every page.
 */
window.LEM = (function () {
  'use strict';

  const PREFIX = 'lem:';
  // How long a cached answer may be shown before it's considered worth a
  // spinner rather than a silent refresh. The refresh happens either way.
  const DEFAULT_MAX_AGE = 5 * 60 * 1000;

  function read(url) {
    try {
      const raw = sessionStorage.getItem(PREFIX + url);
      if (!raw) return null;
      const box = JSON.parse(raw);
      if (!box || typeof box.at !== 'number') return null;
      return box;
    } catch (e) {
      return null;                       // private mode, quota, corrupt entry
    }
  }

  function write(url, data) {
    try {
      sessionStorage.setItem(PREFIX + url,
                             JSON.stringify({at: Date.now(), data: data}));
    } catch (e) {
      // Quota or private browsing. Caching is an optimisation, never a
      // requirement — carry on uncached.
    }
  }

  /* Paint from cache at once, then refresh in the background.
   *
   * render(data, meta) is called up to twice: once with meta.cached === true if
   * something was stored, then once with the fresh answer — but only if it
   * actually differs, so the DOM isn't rebuilt for nothing (which would lose
   * scroll position and any open <details>).
   *
   * `opts.signature(data)` decides what "differs" means. It matters: /api/machines
   * carries `age_seconds`, which changes on EVERY request, so a full JSON compare
   * was always unequal and the page repainted every single time — the comparison
   * did nothing at all. A signature that ignores the clock makes it do its job.
   *
   * Returns the promise for the network half, so a caller can await settling.
   */
  function live(url, render, opts) {
    opts = opts || {};
    const sign = opts.signature || (d => JSON.stringify(d));
    const box = read(url);
    let shown = null;
    if (box && (Date.now() - box.at) < (opts.maxAge || DEFAULT_MAX_AGE)) {
      try {
        shown = sign(box.data);
        render(box.data, {cached: true, at: box.at});
      } catch (e) {
        shown = null;
      }
    }
    return fetch(url, {headers: {'Cache-Control': 'no-cache'}})
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        write(url, data);
        let fresh;
        try { fresh = sign(data); } catch (e) { fresh = JSON.stringify(data); }
        if (fresh !== shown) render(data, {cached: false});
        return data;
      })
      .catch(() => {
        // Offline or LabCore down. If we painted from cache the page still
        // works; if we didn't, tell the caller so it can say so.
        if (shown === null) {
          try {
            render(null, {cached: false, failed: true});
          } catch (e) { /* the page decides how to fail */ }
        }
        return null;
      });
  }

  /* A plain cached GET for one-shot callers. */
  function get(url, opts) {
    return new Promise(resolve => {
      let done = false;
      live(url, (data, meta) => {
        if (!done && (data !== null || meta.failed)) { done = true; resolve(data); }
      }, opts);
    });
  }

  /* Straight to the network, cache updated, no cached paint first.
   *
   * For a periodic refresh this is the right call: the server serves /api/machines
   * from its own in-memory snapshot in well under a millisecond, so the client
   * cache adds a second layer of staleness and buys nothing. Reaching for `get()`
   * on a 30s timer meant the floor painted a cached answer and only showed the
   * fresh one on the NEXT tick — permanently a cycle behind.
   */
  function fresh(url) {
    return fetch(url, {headers: {'Cache-Control': 'no-cache'}})
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => { write(url, data); return data; })
      .catch(() => {
        const box = read(url);           // network gone: last good answer will do
        return box ? box.data : null;
      });
  }

  /* Drop cached answers. Pass a substring to clear only matching URLs — after
   * a write, so the next paint can't show the state before the change. */
  function bust(match) {
    try {
      Object.keys(sessionStorage)
        .filter(k => k.startsWith(PREFIX) && (!match || k.indexOf(match) >= 0))
        .forEach(k => sessionStorage.removeItem(k));
    } catch (e) { /* nothing to clear */ }
  }

  /* Warm the cache for pages the operator is likely to open next, once this
   * page is idle. Costs nothing visible and makes the next click instant. */
  function prefetch(urls) {
    const go = () => (urls || []).forEach(u => {
      if (read(u)) return;                       // already have it
      fetch(u).then(r => r.ok ? r.json() : null)
        .then(d => { if (d) write(u, d); })
        .catch(() => {});
    });
    if ('requestIdleCallback' in window) requestIdleCallback(go, {timeout: 2500});
    else setTimeout(go, 1200);
  }

  return {live: live, get: get, fresh: fresh, bust: bust, prefetch: prefetch,
          MAX_AGE: DEFAULT_MAX_AGE};
})();
