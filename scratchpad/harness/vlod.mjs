/* vlod.mjs — the same frame with only the near LOD, then only the far cards,
 * plus the on-screen distance distribution of each, so "the distance is what
 * fails" can be pinned to one of the two representations. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vlod';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = [];
p.on('console', m => { if (m.type()==='error') errs.push(m.text()); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
fs.writeFileSync(dir+tag+'-both.png', await p.screenshot());
const stats = await p.evaluate(() => {
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const cam = w.camera; cam.updateMatrixWorld();
  const THREE = window.THREE || v.ctx.THREE;
  const fr = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse));
  const cp = cam.position;
  const out = {near: [], far: []};
  const m = new THREE.Matrix4(), pos = new THREE.Vector3();
  for (const e of v.trees) {
    for (const [k, mesh] of [['near', e.near], ['far', e.far]]) {
      for (let i = 0; i < mesh.count; i++) {
        mesh.getMatrixAt(i, m); pos.setFromMatrixPosition(m);
        const d = pos.distanceTo(cp);
        const s = new THREE.Sphere(pos.clone().setY(pos.y + 12), 16);
        if (fr.intersectsSphere(s)) out[k].push(d);
      }
    }
  }
  const q = a => { a.sort((x,y)=>x-y); const f = t => a.length? Math.round(a[Math.floor(t*(a.length-1))]) : -1;
    return {n: a.length, p10: f(0.1), p50: f(0.5), p90: f(0.9), min: f(0)}; };
  return {near: q(out.near), far: q(out.far),
          counts: v.trees.reduce((s,e)=>({near:s.near+e.near.count, far:s.far+e.far.count}),{near:0,far:0})};
});
console.log(JSON.stringify(stats));
await p.evaluate(()=>{ for (const e of window.__lemWorld.subsystems.get('vegetation').trees) e.far.visible=false; });
await p.waitForTimeout(1200);
fs.writeFileSync(dir+tag+'-nearonly.png', await p.screenshot());
await p.evaluate(()=>{ for (const e of window.__lemWorld.subsystems.get('vegetation').trees){ e.far.visible=true; e.near.visible=false; if(e.trunk) e.trunk.visible=false; } });
await p.waitForTimeout(1200);
fs.writeFileSync(dir+tag+'-faronly.png', await p.screenshot());
if (errs.length) console.log('ERRORS', errs.slice(0,5));
await b.close();
