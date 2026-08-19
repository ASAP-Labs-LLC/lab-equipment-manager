/* tz-xsec.mjs — a cross-section through the deepest declared cutting, and
 * through the deepest tunnel, with the earthworks applied and thrown away.
 * A cutting with 1:1 batters is a V; a trench is a pair of vertical walls. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail&cam=top&time=13&hud=0',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);

const PROBE = () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  const spans = rail.earthworks();
  const pick = (kind) => spans.filter(s => s.kind === kind)
    .sort((a, c) => Math.abs(c.maxDepth) - Math.abs(a.maxDepth))[0];
  const out = {};
  for (const kind of ['cut', 'fill', 'tunnel']) {
    const sp = pick(kind);
    if (!sp) continue;
    const P = sp.points, n = (P.length / 3) | 0;
    /* the point in this span where the formation is furthest from the ground */
    let bi = 0, bd = 0;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(P[i * 3 + 1] - t.heightAt(P[i * 3], P[i * 3 + 2]));
      if (d > bd) { bd = d; bi = i; }
    }
    const i0 = Math.max(0, bi - 1), i1 = Math.min(n - 1, bi + 1);
    let tx = P[i1 * 3] - P[i0 * 3], tz = P[i1 * 3 + 2] - P[i0 * 3 + 2];
    const L = Math.hypot(tx, tz) || 1; tx /= L; tz /= L;
    const nx = -tz, nz = tx;
    const cxp = P[bi * 3], cy = P[bi * 3 + 1], czp = P[bi * 3 + 2];
    const prof = [];
    for (let d = -70; d <= 70; d += 5) {
      prof.push(+t.heightAt(cxp + nx * d, czp + nz * d).toFixed(1));
    }
    /* worst slope between adjacent 5m samples, in degrees */
    let steep = 0;
    for (let i = 1; i < prof.length; i++) {
      steep = Math.max(steep, Math.abs(prof[i] - prof[i - 1]) / 5);
    }
    out[kind] = {maxDepthDeclared: +sp.maxDepth.toFixed(1), half: sp.half, batter: sp.batter,
                 formationY: +cy.toFixed(1),
                 groundAtCentre: +t.heightAt(cxp, czp).toFixed(1),
                 worstFaceDeg: +(Math.atan(steep) * 180 / Math.PI).toFixed(1),
                 profileMinus70to70by5: prof};
  }
  return out;
};

console.log('WITH earthworks');
console.log(JSON.stringify(await p.evaluate(PROBE), null, 1));
await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  t._ework = null; t._teardownMeshes();
  t._buildField(); t._buildCore();
  t._buildRing(t.ringSize, t.ringSeg, t.coreSize, 40);
  t._buildOcean(); t._buildHorizon(); t._buildMainland();
});
console.log('WITHOUT earthworks');
console.log(JSON.stringify(await p.evaluate(PROBE), null, 1));
await b.close();
