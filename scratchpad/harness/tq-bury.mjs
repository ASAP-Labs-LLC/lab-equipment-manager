/* tq-bury.mjs — WHICH TRACK is buried, per track, not per route.
 *
 * alignment.mjs walks three station->hub routes. The operator's complaint is
 * about STATION trackwork (platform roads, loading roads, throats), which those
 * routes barely touch. This walks every track rail.js built, at its own frame
 * step, and reports how much of each is below the ground terrain builds — and
 * whether a declared earthworks span covers that chainage, and of what kind.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=top&time=13&hud=0',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail'), terrain = w.subsystems.get('terrain');
  const spans = (rail.earthworks && rail.earthworks()) || [];
  /* index spans by track, so we can ask "is this chainage declared, as what" */
  const byTrack = new Map();
  for (const s of spans) {
    if (!byTrack.has(s.track)) byTrack.set(s.track, []);
    byTrack.get(s.track).push(s);
  }
  const out = {spans: spans.length, tracks: [], totals: {}};
  const T = {buriedPts: 0, pts: 0, worst: 0, byKind: {}, byKindWorst: {}};
  for (const t of rail.tracks || []) {
    const f = t.frames; if (!f || !f.pos) continue;
    const decl = byTrack.get(t.name) || [];
    let buried = 0, n = 0, worst = 0, worstS = 0, buriedM = 0;
    const kinds = {};
    for (let i = 0; i < f.count; i++) {
      const x = f.pos[i * 3], y = f.pos[i * 3 + 1], z = f.pos[i * 3 + 2];
      const g = terrain.heightAt(x, z);
      if (!isFinite(g)) continue;
      n++; T.pts++;
      const d = y - g;                 // railhead above ground
      if (d >= -0.3) continue;
      buried++; T.buriedPts++; buriedM += f.step;
      const s = i * f.step;
      let kind = 'undeclared';
      for (const sp of decl) if (s >= sp.from - 3 && s <= sp.to + 3) { kind = sp.kind; break; }
      /* RAIL_PAD_KEEP: terrain refuses to move earth within 27 m of a bench, so
       * a station throat that needs a cutting cannot get one. Record it. */
      const gu = terrain._railGuard ? terrain._railGuard(x, z) : 1;
      if (gu < 0.99) kind += '/guarded';
      kinds[kind] = (kinds[kind] || 0) + 1;
      T.byKind[kind] = (T.byKind[kind] || 0) + 1;
      if (!T.byKindWorst[kind] || d < T.byKindWorst[kind].d) {
        T.byKindWorst[kind] = {d: +d.toFixed(1), track: t.name, s: +s.toFixed(0),
                               x: +x.toFixed(0), z: +z.toFixed(0)};
      }
      if (d < worst) { worst = d; worstS = s; }
      if (d < T.worst) T.worst = d;
    }
    if (buried) out.tracks.push({track: t.name, klass: t.klass,
      lengthM: Math.round(t.length || 0), pts: n, buriedPts: buried,
      buriedM: Math.round(buriedM), worstM: +worst.toFixed(1),
      worstAtS: Math.round(worstS), declaredSpans: decl.length, kinds});
  }
  out.tracks.sort((a, c) => a.worstM - c.worstM);
  out.totals = {points: T.pts, buried: T.buriedPts, worst: +T.worst.toFixed(1),
                byKind: T.byKind, byKindWorst: T.byKindWorst};
  return out;
}), null, 1));
await b.close();
