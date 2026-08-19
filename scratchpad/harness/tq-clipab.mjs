/* tq-clipab.mjs — the end-clip, ablated in session.
 *
 * `ework` moved (70 -> 72 spans, deepestCut 8.5 -> 8.9 m) in the same session
 * that changed two things in terrain.js: the drainage retune and the span
 * end-clip. This project has been burned repeatedly by attributing a moved
 * number to whichever edit was most recent, so: two page loads, identical in
 * every respect except `window.__lemAblateClip`, which puts the clip back on
 * the span's first and last segment only.
 *
 *   node tq-clipab.mjs
 */
import {chromium} from 'playwright';

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html'
          + '?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + '&cam=far&time=9&weather=clear&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});

const run = async (ablate) => {
  const ctx = await b.newContext({viewport: {width: 1280, height: 720}});
  if (ablate) await ctx.addInitScript(() => { window.__lemAblateClip = true; });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
  await p.goto(URL, {waitUntil: 'load', timeout: 90000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  await p.waitForTimeout(3500);
  const out = await p.evaluate(() => {
    const w = window.__lemWorld;
    const rail = w.subsystems.get('rail'), terr = w.subsystems.get('terrain');
    const ew = rail && typeof rail.earthworks === 'function' ? rail.earthworks() : null;
    const r = {ablated: !!window.__lemAblateClip,
               clipped: terr && terr._ework ? terr._ework.clipped : null,
               segments: terr && terr._ework ? terr._ework.hw.length : null,
               reach: terr && terr._ework ? +terr._ework.reach.toFixed(1) : null};
    if (ew) {
      const by = {}; let len = 0;
      for (const e of ew) { by[e.kind] = (by[e.kind] || 0) + 1; len += e.length || 0; }
      const cuts = ew.filter(e => e.kind === 'cut').map(e => Math.abs(e.maxDepth || 0));
      r.spans = ew.length; r.byKind = by; r.totalLengthM = Math.round(len);
      r.deepestCut = +Math.max(...cuts).toFixed(1);
      r.cutsDeeperThan9m = cuts.filter(v => v > 9).length;
      r.deckM = Math.round(ew.filter(e => e.kind === 'viaduct' || e.kind === 'bridge')
                             .reduce((s, e) => s + (e.length || 0), 0));
      r.tunnelM = Math.round(ew.filter(e => e.kind === 'tunnel')
                               .reduce((s, e) => s + (e.length || 0), 0));
    }
    /* alignment.mjs's own numbers, computed here so the two clip states can be
     * compared in the same way the gate reports them */
    r.routes = [];
    for (const st of (w.plan.stations || []).slice(0, 3)) {
      const rt = rail && rail.route ? rail.route(st.uid) : null;
      if (!rt || !rt.getPointAt) continue;
      const len = rt.totalLength || rt.len || (rt.getLength && rt.getLength());
      const N = 400, pts = [];
      for (let i = 0; i <= N; i++) pts.push(rt.getPointAt(i / N));
      const step = len / N;
      let maxGrade = 0;
      for (let i = 1; i < pts.length - 1; i++) {
        const g = Math.abs(pts[i].y - pts[i - 1].y) / Math.max(1e-6, step) * 100;
        if (g > maxGrade) maxGrade = g;
      }
      let above = 0, below = 0, wA = 0, wB = 0;
      for (const q of pts) {
        const g = terr && terr.heightAt ? terr.heightAt(q.x, q.z) : null;
        if (g === null || !isFinite(g)) continue;
        const d = q.y - g;
        if (d > 0.9) { above++; wA = Math.max(wA, d); }
        if (d < -0.3) { below++; wB = Math.min(wB, d); }
      }
      r.routes.push({uid: st.uid, maxGradePct: +maxGrade.toFixed(2),
                     onEmbankment: above, worstEmbankmentM: +wA.toFixed(1),
                     inCutting: below, worstCuttingM: +wB.toFixed(1)});
    }
    r.errors = 0;
    return r;
  });
  out.pageErrors = errs;
  await ctx.close();
  return out;
};

const on = await run(false);
const off = await run(true);
console.log(JSON.stringify({clipOnAllSegments: on, clipFirstAndLastOnly: off}, null, 1));
await b.close();
