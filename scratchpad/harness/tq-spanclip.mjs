/* tq-spanclip.mjs — does a fill batter still cross into a span it was excluded
 * from?
 *
 * rail.js's finding: terrain.js drops `tunnel`/`viaduct`/`bridge` spans before
 * the grading index is built, but the ADJACENT fill span's batter is a cone
 * around its last segment and keeps growing past the abutment, so earth arrives
 * under a deck that has already been drawn.
 *
 * rr-abut.mjs measures the consequence, but it measures it THROUGH rail's own
 * workaround (it reserves ground by extending deck spans outward by up to 14 m),
 * so the number it reports is the sum of two fixes and moves when the alignment
 * moves. This asks the question directly and with no renderer and no rebuild in
 * it: walk each excluded span's own centreline, and at every station ask
 * terrain's live `_railGrade` how far it lifts the natural ground there. Then
 * ask the SAME question of the rule as it was before the clip — reimplemented
 * here, out of terrain's own `_ework` arrays, so both numbers come from one
 * index in one session and only the rule differs.
 *
 *   node tq-spanclip.mjs
 */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=far&time=9&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(15000);

const out = await p.evaluate(() => {
  const W = window.__lemWorld;
  const t = W.subsystems.get('terrain');
  const r = W.subsystems.get('rail');
  const E = t._ework;
  if (!E) return {error: 'no earthworks index'};
  const spans = r && r.structures ? r.structures : null;
  const clamp = (v, a, c) => (v < a ? a : (v > c ? c : v));

  /* the rule as it stands, and the rule as it stood, from one index */
  const grade = (h, x, z, useClip) => {
    const ix = Math.floor((x - E.x0) / E.cell), iz = Math.floor((z - E.z0) / E.cell);
    if (ix < 0 || iz < 0 || ix >= E.nx || iz >= E.nz) return h;
    const b0 = iz * E.nx + ix;
    let q = E.start[b0];
    const e = E.start[b0 + 1];
    if (q === e) return h;
    let ceil = Infinity, floor = -Infinity, near = 1e9;
    for (; q < e; q++) {
      const i = E.idx[q];
      const vx = E.bx[i] - E.ax[i], vz = E.bz[i] - E.az[i];
      const wx = x - E.ax[i], wz = z - E.az[i];
      const L = vx * vx + vz * vz;
      const tr = L > 1e-9 ? (wx * vx + wz * vz) / L : 0;
      const tt = clamp(tr, 0, 1);
      const dx = wx - vx * tt, dz = wz - vz * tt;
      let f = Math.sqrt(dx * dx + dz * dz) - E.hw[i];
      if (useClip && E.ec) {
        const c = E.ec[i];
        if (c && (tr < 0 ? (c & 1) : (tr > 1 ? (c & 2) : 0))) {
          f += (tr < 0 ? -tr : tr - 1) * Math.sqrt(L) * 5.0;
        }
      }
      if (f > E.reach) continue;
      if (f < near) near = f;
      const yf = E.ay[i] + (E.by[i] - E.ay[i]) * tt;
      if (f <= 0) { if (yf < ceil) ceil = yf; if (yf > floor) floor = yf; continue; }
      const fe = (f * f) / (f + 6.0);
      const c2 = yf + fe * E.sc[i];
      if (c2 < ceil) ceil = c2;
      const fl = yf - fe * E.sf[i];
      if (fl > floor) floor = fl;
    }
    if (near > 1e8) return h;
    const g = t._railGuard(x, z);
    if (g <= 0.001) return h;
    const k = Math.min(1, Math.max(0, near) / 6.0) * 3.0;
    const smin = (a, c, kk) => {
      if (kk <= 1e-6) return Math.min(a, c);
      const hh = Math.max(0, kk - Math.abs(a - c)) / kk;
      return Math.min(a, c) - hh * hh * kk * 0.25;
    };
    let y = h;
    if (ceil < Infinity) y = smin(y, ceil, k);
    if (floor > -Infinity) y = -smin(-y, -floor, k);
    return g >= 0.999 ? y : h + (y - h) * g;
  };

  const rows = [];
  const preRail = (x, z) => {
    const b = t._baseHeight(x, z);
    if (!t.features || !t.design) return b;
    return t._gradeTo(b, t._designAt(x, z), t._distances(x, z, null));
  };
  for (const tr of (r && r.tracks) || []) {
    const f = tr.frames;
    if (!f) continue;
    let ws = [];
    try { ws = tr.earthworks(); } catch (e) { continue; }
    for (const sp of ws) {
      const kind = String(sp.kind || '');
      if (kind !== 'tunnel' && kind !== 'viaduct' && kind !== 'bridge') continue;
      /* Lift as a function of distance INTO the span from each end, which is
       * the shape the question is about: at the boundary itself the embankment
       * IS there and a large lift is correct, and what was wrong was how far in
       * it kept going. Sampled off the frames' own arc length. */
      const s0 = sp.from, s1 = sp.to;
      const prof = [];
      for (const d of [0, 2, 4, 6, 8, 10, 14, 20]) {
        if (s0 + d > s1 - d) break;
        let no = 0, ol = 0, k = 0;
        for (const at of [s0 + d, s1 - d]) {
          const i = Math.round(sp.i0 + (sp.i1 - sp.i0) * (at - s0) / (s1 - s0));
          const q = Math.max(0, Math.min(f.count - 1, i));
          const x = f.pos[q * 3], z = f.pos[q * 3 + 2];
          const hNat = preRail(x, z);
          no += grade(hNat, x, z, true) - hNat;
          ol += grade(hNat, x, z, false) - hNat;
          k++;
        }
        prof.push({inM: d, old: +(ol / k).toFixed(2), now: +(no / k).toFixed(2)});
      }
      let worstNow = -1e9, worstOld = -1e9, sumNow = 0, sumOld = 0, m = 0;
      for (let i = sp.i0; i <= sp.i1; i++) {
        const q = Math.max(0, Math.min(f.count - 1, i));
        const x = f.pos[q * 3], z = f.pos[q * 3 + 2];
        const hNat = preRail(x, z);
        const liftNow = grade(hNat, x, z, true) - hNat;
        const liftOld = grade(hNat, x, z, false) - hNat;
        if (liftNow > worstNow) worstNow = liftNow;
        if (liftOld > worstOld) worstOld = liftOld;
        sumNow += liftNow; sumOld += liftOld; m++;
      }
      if (!m) continue;
      rows.push({track: tr.name, kind,
                 from: +sp.from.toFixed(1), to: +sp.to.toFixed(1), pts: m,
                 worstLiftOld: +worstOld.toFixed(2), worstLiftNow: +worstNow.toFixed(2),
                 meanLiftOld: +(sumOld / m).toFixed(2), meanLiftNow: +(sumNow / m).toFixed(2), prof});
    }
  }
  return {segments: E.segments, clippedSegments: E.clipped, structures: rows.length, rows};
});
console.log(JSON.stringify(out, null, 1));
await b.close();
