import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
for (const t of [6,8,10,12,13,15,17.5,19]) {
  const p = await b.newPage({viewport:{width:640,height:360}});
  await p.goto(process.argv[2].replace('TIME', String(t)), {waitUntil:'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
  await p.waitForTimeout(2200);
  const d = await p.evaluate(() => {
    const w = window.__lemWorld;
    let sun = null; w.scene.traverse(o => { if (o.isDirectionalLight) sun = o; });
    const v = sun.position.clone().sub(sun.target.position).normalize();
    const cam = w.engine.camera;
    const fwd = new (v.constructor)(0,0,-1).applyQuaternion(cam.quaternion);
    return {az: +(Math.atan2(v.x, v.z) * 180 / Math.PI).toFixed(1),
            el: +(Math.asin(v.y) * 180 / Math.PI).toFixed(1),
            intensity: +sun.intensity.toFixed(2),
            camAz: +(Math.atan2(fwd.x, fwd.z) * 180 / Math.PI).toFixed(1)};
  });
  // angle between where the camera looks and where the sun is: >90 = sun behind camera
  const rel = ((d.az - d.camAz + 540) % 360) - 180;
  console.log(`t=${String(t).padEnd(5)} sunAz ${String(d.az).padStart(7)}  el ${String(d.el).padStart(6)}  I ${d.intensity}  camAz ${String(d.camAz).padStart(7)}  sun-vs-view ${rel.toFixed(0)}deg  ${Math.abs(rel) < 90 ? 'BACKLIT/side' : 'SUN BEHIND CAMERA'}`);
  await p.close();
}
await b.close();
