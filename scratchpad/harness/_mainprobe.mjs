import {chromium} from 'playwright';
const mods = process.argv[2] || 'terrain';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&hud=0&cam=wide&time=16&quality=ultra`, {waitUntil:'load', timeout:90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
await page.waitForTimeout(1200);
const o = await page.evaluate(() => {
  const w = window.__lemWorld; const t = w.subsystems.get('terrain');
  const m = t.meshes.find(x => x.name === 'terrain-mainland');
  const out = {found: !!m};
  if (m) {
    const bs = m.geometry.boundingSphere;
    out.center = [Math.round(bs.center.x), Math.round(bs.center.y), Math.round(bs.center.z)];
    out.radius = Math.round(bs.radius);
    out.visible = m.visible; out.tris = m.geometry.index.count/3;
    out.uniforms = {haze: m.material.uniforms.uHaze.value.toArray().map(v=>+v.toFixed(3)),
                    sun: m.material.uniforms.uSunDir.value.toArray().map(v=>+v.toFixed(3))};
    let miny=1e9, maxy=-1e9; const p = m.geometry.attributes.position.array;
    for (let i=1;i<p.length;i+=3){ miny=Math.min(miny,p[i]); maxy=Math.max(maxy,p[i]); }
    out.yRange = [Math.round(miny), Math.round(maxy)];
    out.waterY = +t.waterY.toFixed(1);
    out.mainlandR = Math.round(t.mainlandR);
    /* Project the arc's centre-top to screen. */
    const c = w.camera; c.updateMatrixWorld(true);
    const probe = [];
    for (const frac of [0, 0.25, 0.5]) {
      const i = Math.round((m.geometry.attributes.position.count));
      void i;
    }
    out.probe = probe;
  }
  out.meshNames = t.meshes.map(x=>x.name);
  return out;
});
console.log(JSON.stringify(o, null, 1));
await browser.close();
