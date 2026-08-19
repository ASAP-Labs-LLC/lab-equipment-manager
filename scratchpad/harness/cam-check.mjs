import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for(const c of ['far','wide','','nonsense']){
  const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
  const q = c ? `&cam=${c}` : '';
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain${q}&time=9&weather=clear&hud=0&quality=ultra`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(6000);
  const v=await p.evaluate(()=>{const r=window.__lemWorld.rig, c=window.__lemWorld.camera;
    return {yaw:+r.goalYaw.toFixed(3), pitch:+r.goalPitch.toFixed(3), dist:+(r.goalDistance||r.distance||0).toFixed(1),
            pos:[c.position.x,c.position.y,c.position.z].map(n=>+n.toFixed(1))};});
  console.log(`cam=${(c||'(absent)').padEnd(9)} yaw ${String(v.yaw).padStart(7)}  pitch ${String(v.pitch).padStart(6)}  dist ${String(v.dist).padStart(6)}  pos ${JSON.stringify(v.pos)}`);
  await p.context().close();
}
await b.close();
