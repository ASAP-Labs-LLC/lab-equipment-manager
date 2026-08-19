import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, out = {vegetation: [], buildings: [], other: []};
  const veg = w.scene.getObjectByName('vegetation');
  const bld = w.scene.getObjectByName('buildings');
  const scan = (root, into) => {
    if (!root) return;
    root.traverse(o => {
      if (!o.isMesh) return;
      into.push({name: (o.name || o.material?.name || o.type).slice(0, 30),
                 instanced: !!o.isInstancedMesh, count: o.count || 1,
                 castShadow: o.castShadow, receiveShadow: o.receiveShadow,
                 matType: o.material?.type,
                 transparent: !!o.material?.transparent,
                 alphaTest: o.material?.alphaTest,
                 depthWrite: o.material?.depthWrite,
                 visible: o.visible});
    });
  };
  scan(veg, out.vegetation); scan(bld, out.buildings);
  const sum = a => ({meshes: a.length, casting: a.filter(x => x.castShadow).length,
                     notCasting: a.filter(x => !x.castShadow).map(x => x.name + (x.instanced ? ' x'+x.count : '')).slice(0,8)});
  return {vegetation: sum(out.vegetation), buildings: sum(out.buildings),
          vegDetail: out.vegetation.slice(0, 10)};
}), null, 1));
await b.close();
