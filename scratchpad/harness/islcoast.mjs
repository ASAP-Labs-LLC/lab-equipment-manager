/* islcoast.mjs — put the camera on the shore. The rig frames the plan, which is
 * a kilometre inland, so no preset can look at the one thing this round added. */
import {chromium} from 'playwright';
const a={}; for(let i=2;i<process.argv.length;i++) if(process.argv[i].startsWith('--')) a[process.argv[i].slice(2)]=process.argv[++i];
const url=`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${a.mods||'sky,gi,terrain'}&hud=0&quality=ultra&time=${a.time||16}&weather=${a.weather||'clear'}`+(a.season?`&season=${a.season}`:'');
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1920,height:1080}});
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,200)));
p.on('console',m=>{if(m.type()==='error'&&!/favicon/.test(m.text()))errs.push(m.text().slice(0,200));});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1200);
const info = await p.evaluate(({bearing, back, height, pitch})=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain');
  const th=+bearing;
  /* March out along the bearing to find the waterline, then stand back from it
   * on the land side and look out to sea. */
  let r=t.islandR*0.4, hit=t.islandR;
  for(let i=0;i<600;i++){
    const rr=t.islandR*0.4+i*6;
    const x=t.cx+Math.cos(th)*rr, z=t.cz+Math.sin(th)*rr;
    if(t.heightAt(x,z)<=t.waterY){hit=rr;break;}
  }
  const rr=hit-(+back);
  const tx=t.cx+Math.cos(th)*hit, tz=t.cz+Math.sin(th)*hit;
  w.rig.goalTarget.set(tx, t.waterY+6, tz);
  w.rig.target.set(tx, t.waterY+6, tz);
  Object.assign(w.rig,{goalYaw: th+(+height ? 0 : Math.PI), goalPitch:+pitch, goalDistance:+back});
  w.rig.idleDrift=false; w.rig.apply(1);
  return {coastR:Math.round(hit), groundAtCoast:+t.heightAt(tx,tz).toFixed(1), waterY:+t.waterY.toFixed(1), rr};
},{bearing:a.bearing||1.1, back:a.back||220, height:a.height||30, pitch:a.pitch||0.10});
await p.waitForTimeout(1400);
await p.screenshot({path:a.out||'coast.png'});
console.log(JSON.stringify({...info,errs}));
await b.close();
