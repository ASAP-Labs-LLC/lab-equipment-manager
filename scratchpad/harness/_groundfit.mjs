/* Does the drawn ground stand where ctx.ground() says it does?
 * "Grass won't stick to the floor" is that question from the cover's side:
 * vegetation places every blade at heightAt(x,z), so any mesh vertex drawn
 * somewhere else is a blade hanging in the air or buried. Checked on the
 * meshes' own vertices, which is where the surface actually is. */
import {chromium} from 'playwright';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
await page.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&cam=wide&time=16', {waitUntil:'load', timeout:90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
const o = await page.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const res = {};
  for (const m of t.meshes) {
    if (!/core|ring/.test(m.name)) continue;
    const p = m.geometry.attributes.position.array;
    /* Only vertices an actual triangle uses. A LOD ring keeps the full V*V
     * vertex array and punches its hole in the INDEX, so half the array is
     * orphan vertices sitting under the core — measuring those reports a
     * disagreement that is never drawn. */
    const ix = m.geometry.index.array;
    const used = new Uint8Array(m.geometry.attributes.position.count);
    for (let i = 0; i < ix.length; i++) used[ix[i]] = 1;
    const n = m.geometry.attributes.position.count;
    const stride = Math.max(1, Math.floor(n / 40000));
    let worst = 0, worstAt = null, sum = 0, cnt = 0, over1 = 0, over01 = 0;
    for (let i = 0; i < n; i += stride) {
      if (!used[i]) continue;
      const x = p[i*3] + m.position.x, y = p[i*3+1] + m.position.y, z = p[i*3+2] + m.position.z;
      /* Skirt vertices are dropped below the surface on purpose. */
      const h = t.heightAt(x, z);
      const d = Math.abs(y - h);
      if (y < t.waterY + 0.5 || h < t.waterY + 0.5) continue;
      if (y < h - 20) continue;
      sum += d; cnt++;
      if (d > 0.1) over01++;
      if (d > 1) over1++;
      if (d > worst) { worst = d; worstAt = [Math.round(x), Math.round(z), +y.toFixed(2), +h.toFixed(2)]; }
    }
    res[m.name.replace(/-[0-9.]+$/, '')] = {samples: cnt, meanAbs: +(sum/cnt).toFixed(4),
      pctOver0_1m: +(100*over01/cnt).toFixed(2), pctOver1m: +(100*over1/cnt).toFixed(2),
      worst: +worst.toFixed(3), worstAt};
  }
  /* And the analytic path everything outside the fine field uses. */
  let w2 = 0, at2 = null, s2 = 0, c2 = 0;
  const R = t.islandR + t.coastWobble;
  for (let i = 0; i < 4000; i++) {
    const a = i * 2.399963, r = R * Math.sqrt((i + 0.5) / 4000);
    const x = t.cx + Math.cos(a) * r, z = t.cz + Math.sin(a) * r;
    if (t.heightAt(x, z) < t.waterY + 0.5) continue;
    const d = Math.abs(t.heightAt(x, z) - t._gradedHeight(x, z));
    const edge = Math.max(Math.abs(x - t.cx), Math.abs(z - t.cz)) - t.coreSize/2;
    if (edge > -60 && d > (res._edgeWorst||0)) { res._edgeWorst = +d.toFixed(3); res._edgeAt = [Math.round(x), Math.round(z), Math.round(edge)]; }
    s2 += d; c2++;
    if (d > w2) { w2 = d; at2 = [Math.round(x), Math.round(z)]; }
  }
  res.heightAt_vs_gradedHeight = {samples: c2, meanAbs: +(s2/c2).toFixed(4),
    worst: +w2.toFixed(3), worstAt: at2,
    note: 'dry land only', coreHalf: Math.round(t.coreSize/2)};
  res.islandR = Math.round(t.islandR);
  res.baseR = t._baseR ? {min: Math.round(Math.min(...t._baseR)), max: Math.round(Math.max(...t._baseR)),
    mean: Math.round([...t._baseR].reduce((a,b)=>a+b,0)/t._baseR.length)} : null;
  /* Land area, measured off the surface. */
  let land = 0, tot = 0, maxR = 0;
  const S = t.islandR * 1.4;
  for (let j = 0; j < 320; j++) for (let i = 0; i < 320; i++) {
    const x = t.cx + (i/319 - 0.5)*2*S, z = t.cz + (j/319 - 0.5)*2*S;
    tot++;
    if (t.heightAt(x, z) > t.waterY) { land++; const r = Math.hypot(x-t.cx, z-t.cz); if (r>maxR) maxR=r; }
  }
  res.landAreaKm2 = +(land * (2*S/319)**2 / 1e6).toFixed(3);
  res.landMaxRadius = Math.round(maxR);
  return res;
});
console.log(JSON.stringify(o, null, 1));
await browser.close();
