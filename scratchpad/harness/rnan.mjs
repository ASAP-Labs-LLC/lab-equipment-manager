import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1500);
console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const out = [];
  rail.root.traverse(o => {
    if (!o.geometry) return;
    const a = o.geometry.attributes.position;
    if (!a) return;
    let bad = 0, minY = Infinity, maxY = -Infinity, minX = Infinity, maxX = -Infinity;
    for (let i = 0; i < a.count; i++) {
      const x = a.getX(i), y = a.getY(i), z = a.getZ(i);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) { bad++; continue; }
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    }
    o.geometry.computeBoundingSphere();
    const bs = o.geometry.boundingSphere;
    out.push({name: o.name || o.type, verts: a.count, bad,
              y: [+minY.toFixed(1), +maxY.toFixed(1)],
              x: [+minX.toFixed(1), +maxX.toFixed(1)],
              r: bs ? +bs.radius.toFixed(1) : null,
              mat: o.material?.type, side: o.material?.side});
  });
  return out;
}), null, 1));
await b.close();
