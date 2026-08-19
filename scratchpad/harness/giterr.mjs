import {chromium} from 'playwright';
const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4000);
console.log(JSON.stringify(await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const out = {big: [], sun: {}};
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    const g = o.geometry;
    const r = g?.boundingSphere?.radius;
    if (!(r > 200)) return;
    const bb = g.boundingBox || (g.computeBoundingBox(), g.boundingBox);
    out.big.push({name: o.name || o.type, r: +r.toFixed(0),
      tris: (g.index ? g.index.count : g.attributes.position.count) / 3,
      ey: +(bb.max.y - bb.min.y).toFixed(1),
      foot: +Math.max(bb.max.x - bb.min.x, bb.max.z - bb.min.z).toFixed(0),
      cast: o.castShadow, recv: o.receiveShadow, fc: o.frustumCulled,
      pos: o.position.toArray().map(n => +n.toFixed(0))});
  });
  const hrs = [7, 9, 11, 13, 15, 17, 19];
  out.sun.path = hrs.map(h => { gi._readSky(h);
    return {h, d: gi.sunDirection.toArray().map(n => +n.toFixed(3)),
            elevDeg: +(Math.asin(gi.sunDirection.y) * 180 / Math.PI).toFixed(1),
            aziDeg: +(Math.atan2(gi.sunDirection.x, gi.sunDirection.z) * 180 / Math.PI).toFixed(1)}; });
  gi._readSky(w.timeOfDay);
  out.sun.camFwd = (() => { const v = new (w.ctx.THREE.Vector3)();
    w.camera.getWorldDirection(v); return v.toArray().map(n => +n.toFixed(3)); })();
  out.ao = {strength: gi.uniforms.lemAOStrength.value, map: !!gi.uniforms.lemAOMap.value,
            tierAO: gi.tier?.ao};
  return out;
}), null, 1));
await browser.close();
