/* What camera does the actual /floor page use? Every blind comparison so far
 * was cam=far on the dev harness. If the operator's view is nothing like it,
 * the loop has been judging a framing nobody sees. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
async function look(url, label, waitReady){
  const p=await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
  await p.goto(url,{waitUntil:'load',timeout:60000});
  try{ await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000}); }catch{}
  await p.waitForTimeout(waitReady);
  const v=await p.evaluate(()=>{const w=window.__lemWorld,r=w.rig,c=w.camera;
    return {yaw:+r.goalYaw.toFixed(3),pitch:+r.goalPitch.toFixed(3),
            dist:+(r.goalDistance||r.distance||0).toFixed(1),
            camY:+c.position.y.toFixed(1), fov:+c.fov.toFixed(1)};});
  console.log(`${label.padEnd(22)} yaw ${String(v.yaw).padStart(7)}  pitch ${String(v.pitch).padStart(6)}  dist ${String(v.dist).padStart(6)}  camY ${String(v.camY).padStart(7)}  fov ${v.fov}`);
  await p.context().close();
}
await look('http://127.0.0.1:5612/floor','REAL /floor',16000);
for(const c of ['far','wide'])
  await look(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${c}&time=9&hud=0&quality=ultra`,`harness cam=${c}`,9000);
await b.close();
