import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:960,height:540}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, e = w.engine;
  const u = e._passes.composite.material.uniforms;
  const out = {};
  for (const k of Object.keys(u)) {
    const v = u[k].value;
    if (typeof v === 'number') out[k] = v;
    else if (v && v.toArray) out[k] = v.toArray();
  }
  const lights = [];
  w.scene.traverse(o => { if (o.isLight) lights.push({t:o.type, i:o.intensity, c:o.color.getHexString()}); });
  out.lights = lights;
  out.envSet = !!w.scene.environment;
  out.envIntensity = w.scene.environmentIntensity;
  return out;
}), null, 0));
await b.close();
