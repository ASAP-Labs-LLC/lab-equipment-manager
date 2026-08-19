import {chromium} from 'playwright';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
await page.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&cam=wide&time=16', {waitUntil:'load', timeout:90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
const o = await page.evaluate(() => {
  const w = window.__lemWorld; const t = w.subsystems.get('terrain');
  w.rig.idleDrift=false; w.rig.apply(1);
  const c = w.camera;
  const camB = Math.atan2(c.position.z - t.cz, c.position.x - t.cx) * 180/Math.PI;
  const viewB = Math.atan2(t.cz - c.position.z, t.cx - c.position.x) * 180/Math.PI;
  const F = t.coastMin; const NB = F.length;
  const rows = [];
  for (let i = 0; i < NB; i += 4) {
    const a = (i/NB)*360;
    rows.push([Math.round(a), Math.round(F[i])]);
  }
  return {cx: Math.round(t.cx), cz: Math.round(t.cz), camPos: [Math.round(c.position.x), Math.round(c.position.y), Math.round(c.position.z)],
          camHoriz: Math.round(Math.hypot(c.position.x-t.cx, c.position.z-t.cz)), camBearing: Math.round(camB), viewBearing: Math.round(viewB),
          fov: c.fov, aspect: +c.aspect.toFixed(3), waterY: +t.waterY.toFixed(1), islandR: Math.round(t.islandR),
          siteRadial: Math.round(t.siteRadial), siteReach: Math.round(t.siteReach), coreSize: Math.round(t.coreSize),
          ringSize: Math.round(t.ringSize), ringSeg: t.ringSeg, target: [Math.round(w.rig.target.x), Math.round(w.rig.target.y), Math.round(w.rig.target.z)],
          coastMin: rows};
});
console.log(JSON.stringify(o));
await browser.close();
