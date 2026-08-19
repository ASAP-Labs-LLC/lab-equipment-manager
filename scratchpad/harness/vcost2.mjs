/* vcost2.mjs — the scene's draw/triangle totals at the judge camera, sampled
 * over several seconds (the shadow map rebuilds on demand, so a single read is
 * as likely to be the cheap frame as the dear one), then the same with
 * vegetation hidden. The difference is this file's bill. */
import {chromium} from 'playwright';
const url = process.argv[2];
const [yaw, pitch, dist] = process.argv.slice(3, 6);
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(9000);
await p.evaluate(({yaw,pitch,dist})=>{ const w=window.__lemWorld;
  const s=w.plan&&w.plan.byUid.get('multitek-ns');
  Object.assign(w.rig,{goalYaw:+yaw,goalPitch:+pitch,goalDistance:+dist});
  w.rig.goalTarget.set(s?s.x:0,4,s?s.z:0); w.rig.apply(1); w.rig.idleDrift=false; },
  {yaw,pitch,dist});
const sample = async () => {
  const out = [];
  for (let i = 0; i < 14; i++) {
    await p.waitForTimeout(320);
    out.push(await p.evaluate(()=>window.__lemWorld.stats()));
  }
  const d = out.map(s=>s.drawCalls).sort((a,b)=>a-b);
  const t = out.map(s=>s.triangles).sort((a,b)=>a-b);
  return {drawMax: d[d.length-1], triMax: t[t.length-1],
          drawMed: d[7], triMed: t[7], fps: out[out.length-1].fps};
};
const on = await sample();
await p.evaluate(()=>{ const v=window.__lemWorld.subsystems.get('vegetation');
  v.group.visible = false; window.__lemWorld.engine.shadowNeedsUpdate = true; });
const off = await sample();
console.log(JSON.stringify({on, off,
  vegDraws: on.drawMax - off.drawMax, vegTris: on.triMax - off.triMax}));
await b.close();
