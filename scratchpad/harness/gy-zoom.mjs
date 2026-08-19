/* gy-zoom.mjs — the near map is PARKED at cam=far. Prove it comes back when the
 * operator zooms in, and goes away again, in one session. A map that parked and
 * stayed parked would be a whole session with no near shadows and no error. */
import {chromium} from 'playwright';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(10000);
const snap = async (tag) => console.log(tag, JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  return {dist: +w.rig.distance.toFixed(0), parked: !!gi._nearParked,
          nearR: +gi.uniforms.lemNearRadius.value.toFixed(3),
          fitR: gi._shadowFit.radius,
          casters: gi._cullable.filter(o => o.castShadow).length,
          useful: +gi._nearUsefulRange().toFixed(0), dNear: +(gi._vg?.dNear ?? -1).toFixed(0),
          draws: w.engine.renderer.info.render.calls};
})));
await snap('far      ');
for (const d of [500, 300, 150, 900]) {
  await p.evaluate((d) => { const w = window.__lemWorld; w.rig.goalDistance = d; }, d);
  await p.waitForTimeout(6000);
  await snap('dist ' + String(d).padStart(4));
}
await b.close();
