/* tz-tun.mjs — who is digging over the tunnel? At the deepest point of the
 * deepest tunnel bore, list every declared span with a point within 60m. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail&cam=top&time=13&hud=0',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  const spans = rail.earthworks();
  const byKind = {};
  for (const s of spans) {
    byKind[s.kind] = byKind[s.kind] || {n: 0, len: 0, worst: 0};
    byKind[s.kind].n++; byKind[s.kind].len += s.length;
    byKind[s.kind].worst = Math.max(byKind[s.kind].worst, Math.abs(s.maxDepth));
  }
  const tun = spans.filter(s => s.kind === 'tunnel')
                   .sort((a, c) => Math.abs(c.maxDepth) - Math.abs(a.maxDepth))[0];
  if (!tun) return {byKind, note: 'no tunnel'};
  const P = tun.points, n = (P.length / 3) | 0;
  let bi = 0, bd = 0;
  for (let i = 0; i < n; i++) {
    const d = Math.abs(P[i * 3 + 1] - t.heightAt(P[i * 3], P[i * 3 + 2]));
    if (d > bd) { bd = d; bi = i; }
  }
  const X = P[bi * 3], Y = P[bi * 3 + 1], Z = P[bi * 3 + 2];
  const near = [];
  for (const s of spans) {
    const Q = s.points; if (!Q) continue;
    let best = Infinity, by = 0;
    for (let i = 0; i < Q.length; i += 3) {
      const d = Math.hypot(Q[i] - X, Q[i + 2] - Z);
      if (d < best) { best = d; by = Q[i + 1]; }
    }
    if (best < 60) near.push({track: s.track, kind: s.kind, dist: +best.toFixed(1),
                              formationY: +by.toFixed(1), maxDepth: +s.maxDepth.toFixed(1),
                              from: Math.round(s.from), to: Math.round(s.to)});
  }
  near.sort((a, c) => a.dist - c.dist);
  return {byKind, tunnel: {track: tun.track, from: Math.round(tun.from), to: Math.round(tun.to),
          maxDepth: +tun.maxDepth.toFixed(1), atX: Math.round(X), atZ: Math.round(Z),
          formationY: +Y.toFixed(1), groundNow: +t.heightAt(X, Z).toFixed(1)},
          spansWithin60m: near};
}), null, 1));
await b.close();
