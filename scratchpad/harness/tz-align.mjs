/* tz-align.mjs — alignment.mjs's cutting measurement, but attributed: for every
 * sample of the route that sits below the ground, WHICH declared span is it in?
 * A tunnel bore is supposed to be below ground; an open cutting is not. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail,trains&cam=top&time=13&hud=0',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail'), terrain = w.subsystems.get('terrain');
  const spans = (rail.earthworks && rail.earthworks()) || [];
  /* Nearest declared span point to a world position, and its kind. */
  const nearest = (x, z) => {
    let best = Infinity, kind = 'none', dep = 0;
    for (const sp of spans) {
      const P = sp.points; if (!P) continue;
      for (let i = 0; i < P.length; i += 3) {
        const dx = P[i] - x, dz = P[i + 2] - z;
        const d = dx * dx + dz * dz;
        if (d < best) { best = d; kind = sp.kind; dep = sp.maxDepth; }
      }
    }
    return {d: Math.sqrt(best), kind, dep};
  };
  const guardAt = (x, z) => terrain._railGuard ? terrain._railGuard(x, z) : 1;
  const out = {spans: spans.length, routes: []};
  for (const st of w.plan.stations.slice(0, 3)) {
    const r = rail.route ? rail.route(st.uid) : null;
    if (!r || !r.getPointAt) continue;
    const N = 400, by = {}, worstByKind = {};
    let worst = 0, worstInfo = null, below = 0;
    for (let i = 0; i <= N; i++) {
      const q = r.getPointAt(i / N);
      const g = terrain.heightAt(q.x, q.z);
      if (!isFinite(g)) continue;
      const d = q.y - g;
      if (d >= -0.3) continue;
      below++;
      const nb = nearest(q.x, q.z);
      const key = nb.d > 14 ? 'unclaimed' : nb.kind;
      by[key] = (by[key] || 0) + 1;
      if (!worstByKind[key] || d < worstByKind[key].d) {
        worstByKind[key] = {d: +d.toFixed(1), spanDist: +nb.d.toFixed(1),
                            guard: +guardAt(q.x, q.z).toFixed(2)};
      }
      if (d < worst) { worst = d; worstInfo = {kind: key, spanDist: +nb.d.toFixed(1),
                                              guard: +guardAt(q.x, q.z).toFixed(2)}; }
    }
    out.routes.push({uid: st.uid, samplesBelowGround: below, worst: +worst.toFixed(1),
                     worstInfo, byKind: by, worstByKind});
  }
  return out;
}), null, 1));
await b.close();
