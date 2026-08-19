import {chromium} from 'playwright';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(11000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const casters = [];
  w.scene.traverse(o => { if ((o.isMesh||o.isInstancedMesh) && o.castShadow && o.parent)
    casters.push({n:(o.name||o.type).slice(0,44), base:o.userData?.lemCastBase, inCullable: gi._cullable.includes(o)}); });
  return {parked: !!gi._nearParked, fitRadius: gi._shadowFit.radius,
    nearRadiusUniform: gi.uniforms.lemNearRadius.value,
    cullable: gi._cullable.length,
    cullableCasting: gi._cullable.filter(o=>o.castShadow).length,
    casters: casters.length, sample: casters.slice(0,14),
    usefulRange: gi._nearUsefulRange(), vg: gi._vg && {dNear: gi._vg.dNear, dAim: gi._vg.dAim},
    sunCast: gi.sun.castShadow, draws: w.engine.renderer.info.render.calls};
}), null, 1));
await b.close();
