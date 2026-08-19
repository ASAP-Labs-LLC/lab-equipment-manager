import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1600,height:900}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(3000);
const info = await p.evaluate(() => {
  const w = window.__lemWorld, r = w.engine.renderer;
  r.shadowMap.autoUpdate = true;                    // brute force: every frame
  const sun = []; w.scene.traverse(o => { if (o.isDirectionalLight) sun.push(o); });
  const s = sun[0];
  const out = {targetInScene: false, targetPos: null, sunPos: null};
  if (s) {
    out.sunPos = [s.position.x|0, s.position.y|0, s.position.z|0];
    out.targetPos = [s.target.position.x|0, s.target.position.y|0, s.target.position.z|0];
    let n = s.target, inScene = false;
    while (n) { if (n === w.scene) inScene = true; n = n.parent; }
    out.targetInScene = inScene;
    out.targetMatrixAutoUpdate = s.target.matrixAutoUpdate;
  }
  return out;
});
console.log(JSON.stringify(info));
await p.waitForTimeout(1500);
await p.screenshot({path: process.argv[3]});
await b.close();
