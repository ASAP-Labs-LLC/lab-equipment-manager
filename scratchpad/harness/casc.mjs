import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
for (const cam of ['yard','low','street']) {
  const p = await b.newPage({viewport:{width:1280,height:720}});
  const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=${cam}&time=13&weather=clear`;
  await p.goto(url, {waitUntil:'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
  await p.waitForTimeout(3500);
  const r = await p.evaluate(() => {
    const w = window.__lemWorld, out = [];
    w.scene.traverse(o => {
      if (o.isDirectionalLight && o.castShadow && o.shadow) {
        const c = o.shadow.camera;
        out.push({intensity:+o.intensity.toFixed(2), map:o.shadow.mapSize.width,
                  span:Math.round(c.right-c.left), near:Math.round(c.near), far:Math.round(c.far),
                  hasMap: !!o.shadow.map});
      }
    });
    return {cam: null, lights: out, camDist: Math.round(w.rig.distance),
            pitch: +(w.rig.pitch).toFixed(2)};
  });
  console.log(cam.padEnd(7), 'dist', String(r.camDist).padStart(4), 'pitch', r.pitch,
              '| cascades:', JSON.stringify(r.lights));
  await p.close();
}
await b.close();
