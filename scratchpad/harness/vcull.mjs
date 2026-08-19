import {chromium} from 'playwright';
const [url, dir] = process.argv.slice(2);
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3000);
for (const R of [99999, 900, 750, 620]) {
  await p.evaluate(async r => {
    const THREE = await import('three');
    const v = window.__lemWorld.subsystems.get('vegetation');
    const cam = window.__lemWorld.engine.camera;
    for (const e of v.trees) {
      const a = e.far.instanceMatrix.array;
      let n = 0;
      // rebuild from the module's own store, culling by radius
      v._repartition(true);
    }
    // now prune in place
    for (const e of v.trees) {
      const a = e.far.instanceMatrix.array;
      const t = e.far.geometry.getAttribute('aVegTint').array;
      let n = 0;
      for (let i = 0; i < e.far.count; i++) {
        const x = a[i*16+12], z = a[i*16+14];
        const d = Math.hypot(x-cam.position.x, z-cam.position.z);
        if (d > r) continue;
        a.copyWithin(n*16, i*16, i*16+16);
        t.copyWithin(n*3, i*3, i*3+3);
        n++;
      }
      e.far.count = n;
      e.far.instanceMatrix.needsUpdate = true;
      e.far.geometry.getAttribute('aVegTint').needsUpdate = true;
    }
  }, R);
  await p.waitForTimeout(400);
  await p.screenshot({path:`${dir}/cull-${R}.png`});
  console.log(R);
}
await b.close();
