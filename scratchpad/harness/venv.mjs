/* venv.mjs — sweep the foliage materials' envMapIntensity on the fixed judge
 * camera. The environment is the only blue light a leaf gets in this world, so
 * it is the lever on canopy saturation that is not a repaint. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3];
const vals = JSON.parse(process.argv[4]);
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(9000);
await p.evaluate(()=>{ const w=window.__lemWorld;
  const s=w.plan&&w.plan.byUid.get('multitek-ns');
  Object.assign(w.rig,{goalYaw:1.95,goalPitch:0.06,goalDistance:62});
  w.rig.goalTarget.set(s?s.x:0,4,s?s.z:0); w.rig.apply(1); w.rig.idleDrift=false; });
await p.waitForTimeout(2500);
for (const v of vals) {
  await p.evaluate(({v})=>{ const g=window.__lemWorld.subsystems.get('vegetation');
    for (const k of ['matNear','matFar','matClutter','matGrass'])
      if (g[k]) g[k].envMapIntensity = v;
  }, {v});
  await p.waitForTimeout(1200);
  fs.writeFileSync(dir+tag+'-'+v+'.png', await p.screenshot());
  console.log(tag+'-'+v);
}
await b.close();
