/* rr-ends.mjs — enumerate every DRAWN track end in the live scene and say what
 * terminates it. No opinions from the code: it walks the geometry that is
 * actually laid (renderFrom..renderTo, less the tunnel bores) and asks, for
 * each free end, whether any other rail continues within reach and whether a
 * buffer stop stands on it.
 *
 *   node rr-ends.mjs [--layouts 4]
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) args[process.argv[i].slice(2)] = process.argv[i + 1];
}
const layouts = parseInt(args.layouts || '4', 10);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));

const probe = () => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = rail.tracks.filter(t => t.frames);
  const spanOf = (t) => {
    const lo = t.renderFrom || 0, hi = Math.min(t.renderTo, t.length);
    if (!(hi > lo)) return [];
    let bores = [];
    try { t.earthworks(); bores = t.bores || []; } catch { bores = []; }
    if (!bores.length) return [[lo, hi]];
    const out = []; let at = lo;
    for (const [a, c] of [...bores].sort((x, y) => x[0] - y[0])) {
      if (a > at + 1) out.push([at, Math.min(a, hi)]);
      at = Math.max(at, c);
    }
    if (hi > at + 1) out.push([at, hi]);
    return out.filter(r => r[1] > r[0] + 1);
  };
  const pt = (t, s) => {
    const f = t.frames;
    const i = Math.max(0, Math.min(f.count - 1, Math.round(s / f.step)));
    return [f.pos[i * 3], f.pos[i * 3 + 1], f.pos[i * 3 + 2]];
  };
  /* every drawn centreline point in the world, tagged with its owner */
  const cloud = [];
  const spans = new Map();
  for (const t of T) {
    const ss = spanOf(t);
    spans.set(t.name, ss);
    for (const [lo, hi] of ss) {
      for (let s = lo; s <= hi; s += 1.5) cloud.push([...pt(t, s), t.name]);
    }
  }
  /* buffer stops, READ from rail rather than re-derived: reproducing the rule
   * here is how this probe first reported two terminated headshunts as
   * unterminated. */
  const stops = (rail.bufferStops || []).map(b => [b.x, b.y, b.z, b.track]);
  /* tunnel portals: the mouths, both ends of every bore */
  const portals = [];
  for (const t of T) {
    for (const [a, c] of t.bores || []) {
      portals.push([...pt(t, a), t.name]); portals.push([...pt(t, c), t.name]);
    }
  }
  const d2 = (a, x, z) => Math.hypot(a[0] - x, a[2] - z);

  const ends = [];
  for (const t of T) {
    const ss = spans.get(t.name);
    for (const [lo, hi] of ss) {
      for (const [s, which] of [[lo, 'lo'], [hi, 'hi']]) {
        const q = pt(t, s);
        /* nearest OTHER rail that is drawn */
        let dOther = Infinity, who = null;
        for (const c of cloud) {
          if (c[3] === t.name) continue;
          const d = d2(c, q[0], q[2]);
          if (d < dOther) { dOther = d; who = c[3]; }
        }
        /* nearest point of this same track, elsewhere (a loop closing on
         * itself, or the far side of a bore) */
        let dSelf = Infinity;
        for (const [l2, h2] of ss) {
          if (l2 === lo && h2 === hi) continue;
          for (let u = l2; u <= h2; u += 1.5) {
            const d = d2(pt(t, u), q[0], q[2]); if (d < dSelf) dSelf = d;
          }
        }
        let dStop = Infinity;
        for (const st of stops) dStop = Math.min(dStop, d2(st, q[0], q[2]));
        let dPortal = Infinity;
        for (const pr of portals) dPortal = Math.min(dPortal, d2(pr, q[0], q[2]));
        ends.push({track: t.name, which, s: +s.toFixed(1),
                   x: +q[0].toFixed(1), z: +q[2].toFixed(1),
                   dOther: +Math.min(dOther, 999).toFixed(2), who,
                   dSelf: +Math.min(dSelf, 999).toFixed(2),
                   dStop: +Math.min(dStop, 999).toFixed(2),
                   dPortal: +Math.min(dPortal, 999).toFixed(2)});
      }
    }
  }
  /* classify: a junction if another road continues within 6m; a portal if a
   * bore mouth is on it; terminated if a buffer stop is on it; otherwise the
   * rail simply stops. */
  for (const e of ends) {
    e.kind = e.dPortal < 2 ? 'portal'
           : e.dStop < 4 ? 'buffer'
           : (e.dOther < 6 || e.dSelf < 6) ? 'junction'
           : 'NOWHERE';
  }
  return {
    tracks: T.map(t => ({name: t.name, len: +t.length.toFixed(1),
                         from: +(t.renderFrom || 0).toFixed(1),
                         to: +Math.min(t.renderTo, t.length).toFixed(1),
                         bores: (t.bores || []).length,
                         inLines: rail.lines.includes(t)})),
    ends, stops: stops.length, portals: portals.length,
    dead: rail.deadTracks, structures: rail.structures,
  };
};

const rows = [];
for (let L = 0; L < layouts; L++) {
  const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail` +
              `&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`;
  await p.goto(url, {waitUntil: 'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  await p.waitForTimeout(2500);
  const r = await p.evaluate(probe);
  const bad = r.ends.filter(e => e.kind === 'NOWHERE');
  console.log(`--- layout ${L} --- tracks ${r.tracks.length}  ends ${r.ends.length}  ` +
              `stops ${r.stops}  portals ${r.portals}  dead ${JSON.stringify(r.dead)}`);
  const tally = {};
  for (const e of r.ends) tally[e.kind] = (tally[e.kind] || 0) + 1;
  console.log('   ', JSON.stringify(tally));
  for (const e of bad) {
    console.log(`    NOWHERE  ${e.track} ${e.which} s=${e.s} at (${e.x},${e.z}) ` +
                `nearestOtherRail=${e.dOther}m (${e.who}) nearestStop=${e.dStop}m`);
  }
  if (L === 0) {
    for (const t of r.tracks) {
      console.log(`      ${t.inLines ? 'LINE ' : 'road '}${t.name} len=${t.len} ` +
                  `drawn ${t.from}..${t.to} bores=${t.bores}`);
    }
  }
  rows.push({L, nowhere: bad.length, ends: r.ends.length});
}
console.log('\nTOTAL nowhere ends:', rows.reduce((a, r) => a + r.nowhere, 0),
            'over', rows.reduce((a, r) => a + r.ends, 0), 'ends');
await b.close();
