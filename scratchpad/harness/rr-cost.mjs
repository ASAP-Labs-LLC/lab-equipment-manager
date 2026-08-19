/* rr-cost.mjs — what the bridges and the portals cost, ablated in one session.
 * Two scene totals taken from two files on disk measure every builder who was
 * awake; hiding the named meshes and re-reading the same frame measures mine. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:760}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail,buildings&cam=wide&time=9&quality=ultra&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld;
  const names = ['rail.structures.masonry','rail.structures.steel'];
  const found = names.map(n => w.scene.getObjectByName(n)).filter(Boolean);
  const med = async () => {
    const xs = [];
    for (let i = 0; i < 10; i++) {
      await new Promise(r => requestAnimationFrame(r));
      const R = w.renderer || w.engine?.renderer || w.engine?.r;
      const i = R?.info?.render;
      const s = w.stats ? w.stats() : {};
      xs.push([i ? i.calls : (s.drawCalls ?? s.calls ?? -1),
               i ? i.triangles : (s.triangles ?? -1)]);
    }
    xs.sort((a, c) => a[1] - c[1]);
    return xs[5];
  };
  const on = await med();
  for (const m of found) m.visible = false;
  const off = await med();
  for (const m of found) m.visible = true;
  return {meshes: found.map(m => ({name: m.name,
            tris: m.geometry.attributes.position.count / 3})),
          statsKeys: Object.keys(w.stats ? w.stats() : {}),
          hasRenderer: !!(w.renderer || w.engine?.renderer),
          withStructures: on, without: off,
          deltaDraws: on[0] - off[0], deltaTris: on[1] - off[1]};
}), null, 1));
await b.close();
