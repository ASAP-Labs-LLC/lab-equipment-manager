/* _rrbind.mjs — walk each deck span's centreline and name the SEGMENT of
 * terrain's own _ework index that binds the floor (i.e. does the filling),
 * then map that segment back to the published span it came from. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(6000);
const out = await p.evaluate(() => {
  const W = window.__lemWorld;
  const t = W.subsystems.get('terrain'), r = W.subsystems.get('rail');
  const E = t._ework;
  const spans = r.earthworks();
  const RAIL_ROUND = t.constructor.RAIL_ROUND;
  // map each index segment to a span: nearest span whose points contain ax,az
  const owner = new Array(E.segments).fill(null);
  for (let i = 0; i < E.segments; i++) {
    let best = 1e9, bs = null;
    for (const sp of spans) {
      const P = sp.points, n = P.length / 3 | 0;
      for (let q = 0; q < n; q++) {
        const d = Math.hypot(P[q*3] - E.ax[i], P[q*3+2] - E.az[i]);
        if (d < best) { best = d; bs = {sp, q, n}; }
      }
    }
    owner[i] = bs && best < 0.01
      ? `${bs.sp.track} ${bs.sp.kind} ${bs.sp.from.toFixed(0)}-${bs.sp.to.toFixed(0)} pt${bs.q}/${bs.n-1}`
      : '?';
  }
  const clamp = (v,a,c) => v<a?a:(v>c?c:v);
  const rows = [];
  for (const sp of spans) {
    if (sp.kind !== 'viaduct' && sp.kind !== 'bridge') continue;
    const P = sp.points, n = P.length / 3 | 0;
    const prof = [];
    for (let k = 0; k < n; k += Math.max(1, Math.floor(n / 12))) {
      const x = P[k*3], z = P[k*3+2];
      const ix = Math.floor((x-E.x0)/E.cell), iz = Math.floor((z-E.z0)/E.cell);
      if (ix<0||iz<0||ix>=E.nx||iz>=E.nz) { prof.push({k, none:1}); continue; }
      const bkt = iz*E.nx+ix;
      let floor = -Infinity, fseg = -1, ffloorF = 0;
      for (let q = E.start[bkt], e = E.start[bkt+1]; q < e; q++) {
        const i = E.idx[q];
        const vx=E.bx[i]-E.ax[i], vz=E.bz[i]-E.az[i];
        const wx=x-E.ax[i], wz=z-E.az[i];
        const L=vx*vx+vz*vz;
        const tr = L>1e-9 ? (wx*vx+wz*vz)/L : 0;
        const tt = clamp(tr,0,1);
        const dx=wx-vx*tt, dz=wz-vz*tt;
        let f = Math.hypot(dx,dz) - E.hw[i];
        const clip = E.ec[i];
        if (clip && (tr<0 ? (clip&1) : (tr>1 ? (clip&2) : 0))) {
          f += (tr<0 ? -tr : tr-1)*Math.sqrt(L)*5.0;
        }
        if (f > E.reach) continue;
        const yf = E.ay[i] + (E.by[i]-E.ay[i])*tt;
        let fl;
        if (f <= 0) fl = yf;
        else fl = yf - ((f*f)/(f+4.0))*E.sf[i];
        if (fl > floor) { floor = fl; fseg = i; ffloorF = f; }
      }
      prof.push({k, inM: +(k*sp.step).toFixed(0),
                 natural: +t.heightAt(x,z).toFixed(2),
                 formY: +P[k*3+1].toFixed(2),
                 floor: +floor.toFixed(2), f: +ffloorF.toFixed(2),
                 by: fseg>=0 ? owner[fseg] : 'none',
                 clip: fseg>=0 ? E.ec[fseg] : 0});
    }
    rows.push({span: `${sp.track} ${sp.kind} ${sp.from.toFixed(1)}-${sp.to.toFixed(1)}`,
               nPts: n, step: sp.step, prof});
  }
  return {segments: E.segments, reach: +E.reach.toFixed(1), rows};
});
console.log('segments', out.segments, 'reach', out.reach);
for (const r of out.rows) {
  console.log(`\n${r.span}  pts=${r.nPts} step=${r.step}`);
  for (const q of r.prof) {
    if (q.none) { console.log(`  ${q.k}: outside index`); continue; }
    console.log(`  +${String(q.inM).padStart(3)}m form ${String(q.formY).padStart(7)} floor ${String(q.floor).padStart(7)} (f=${q.f}, clipbits ${q.clip})  by ${q.by}`);
  }
}
await b.close();
