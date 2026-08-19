/* wslope.mjs — how much of the core is actually steep enough for the triplanar
 * branch to fire, and where the earthwork creases are. Reports the dihedral
 * angle between adjacent core cells so "faceted prism with hard creases" is a
 * number rather than an impression. */
import {chromium} from 'playwright';
const [,, url] = process.argv;
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(url + '&hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(2500);
const res = await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('terrain');
  const c = T.core, V = c.V, h = c.h, s = c.step;
  let fires = 0, n = 0, maxSlope = 0;
  const bins = new Array(10).fill(0);
  let worstKink = 0, kinks = 0;
  for (let j = 1; j < V - 2; j++) {
    for (let i = 1; i < V - 2; i++) {
      const k = j * V + i;
      const gx = (h[k + 1] - h[k - 1]) / (2 * s);
      const gz = (h[k + V] - h[k - V]) / (2 * s);
      const len = Math.hypot(gx, gz, 1);
      const ny = 1 / len, nx = -gx / len, nz = -gz / len;
      let ax = Math.abs(nx) ** 3, ay = Math.abs(ny) ** 3, az = Math.abs(nz) ** 3;
      const t = ax + ay + az; ax /= t; ay /= t; az /= t;
      if (ax + az > 0.02) fires++;
      const slope = Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI;
      maxSlope = Math.max(maxSlope, slope);
      bins[Math.min(9, Math.floor(slope / 6))]++;
      n++;
      /* dihedral: the change in slope from one cell to the next along x */
      const s0 = (h[k] - h[k - 1]) / s, s1 = (h[k + 1] - h[k]) / s;
      const kink = Math.abs(Math.atan(s1) - Math.atan(s0)) * 180 / Math.PI;
      if (kink > worstKink) worstKink = kink;
      if (kink > 8) kinks++;
    }
  }
  return {step: s, verts: n, triplanarPct: (100 * fires / n).toFixed(1),
          maxSlope: maxSlope.toFixed(1),
          slopeHist: bins.map((v, i) => `${i * 6}-${i * 6 + 6}°:${(100 * v / n).toFixed(1)}%`),
          worstKink: worstKink.toFixed(1), kinkPct: (100 * kinks / n).toFixed(2)};
});
console.log(JSON.stringify(res, null, 1));
await b.close();
