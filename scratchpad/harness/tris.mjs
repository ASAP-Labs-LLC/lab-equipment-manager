import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const tally = {};
  const count = g => {
    let tris = 0, meshes = 0, instances = 0;
    g.traverse(o => {
      if (!o.isMesh) return;
      meshes++;
      const gm = o.geometry;
      if (!gm || !gm.attributes || !gm.attributes.position) return;
      const n = gm.index ? gm.index.count / 3 : gm.attributes.position.count / 3;
      const inst = o.isInstancedMesh ? o.count : 1;
      instances += inst;
      tris += n * inst;
    });
    return {tris: Math.round(tris), meshes, instances};
  };
  // Attribute each top-level child of the scene to whichever subsystem owns it.
  for (const child of w.scene.children) {
    const key = child.name || child.type;
    const c = count(child);
    if (!tally[key]) tally[key] = {tris: 0, meshes: 0, instances: 0};
    tally[key].tris += c.tris;
    tally[key].meshes += c.meshes;
    tally[key].instances += c.instances;
  }
  const total = Object.values(tally).reduce((s, v) => s + v.tris, 0);
  return {total, tally, rendered: w.stats()};
}), null, 1));
await b.close();
