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

  /* Turn a failed write into a sentence worth showing a supervisor.
   *
   * Three things the server now sends that every save handler on the floor used
   * to throw away:
   *
   * - `error`. Half the handlers replaced it with a canned string ("Could not
   *   apply the override."), which tells the person nothing about whether to
   *   try again in five seconds or go and find someone.
   * - `retry_after`. LabCore says how long it wants to be left alone, the
   *   server passes it on, and a floor that drops it makes people either give
   *   up on a save that would have worked or hammer a queue that has just said
   *   it is full.
   * - `landed` / `not_landed`. There is no transaction across LabCore queue
   *   operations, so a multi-statement save really can half-happen. Saying so
   *   is the whole point; hiding it leaves someone to discover it later.
   *
   * Deliberately returns a string rather than painting anything: the callers
   * put their errors in different places (a dialog's error line, an alert) and
   * this has no business choosing.
   */
  function failure(response, body, fallback) {
    body = body || {};
    let text = body.error || fallback || 'That did not save.';
    if (body.not_landed && body.not_landed.length) {
      if (body.landed && body.landed.length) {
        text += ' Saved: ' + body.landed.join(', ') + '.';
      }
      text += ' NOT saved: ' + body.not_landed.join(', ') + '.';
    }
    // Only for a refusal that is actually worth retrying. Telling someone to
    // come back in five seconds for a write that will be refused forever is
    // its own kind of lie.
    if (body.retryable && body.retry_after > 0) {
      text += ' Try again in ' + Math.ceil(body.retry_after) + 's.';
    } else if (body.retryable) {
      text += ' Try again shortly.';
    }
    return text;
  }

  /* fetch + parse + format, for a write. Resolves to {ok, status, body, error}
   * — never rejects, because a save handler that throws leaves the dialog open
   * with a spinner and no explanation, which is the failure this exists to
   * remove. A network error reads as a failure with a sentence, like any
   * other. */
  function send(url, options) {
    options = options || {};
    const init = {method: options.method || 'POST',
                  headers: {'Content-Type': 'application/json'}};
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
    return fetch(url, init).then(r =>
      r.json().catch(() => ({})).then(body => ({
        ok: r.ok, status: r.status, body: body,
        error: r.ok ? '' : failure(r, body, options.fallback)
      }))
    ).catch(() => ({
      ok: false, status: 0, body: {},
      error: options.fallback
        ? options.fallback + ' The server could not be reached.'
        : 'The server could not be reached, so nothing was saved.'
    }));
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
          failure: failure, send: send, MAX_AGE: DEFAULT_MAX_AGE};
})();
