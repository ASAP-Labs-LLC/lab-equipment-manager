/* Verify on the running world: the top of a wheel must move forward faster
 * than the wheel's centre. Sample the real instance matrix. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1000,height:700}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(14000);
console.log(JSON.stringify(await p.evaluate(()=>new Promise(res=>{
  const THREE=window.__lemWorld.ctx.THREE, T=window.__lemWorld.subsystems.get('trains');
  const m=new THREE.Matrix4(); let prevC=null, prevT=null, got=[], n=0;
  const tick=()=>{
    const c=(T.consists||[]).find(x=>x&&x.state==='out'&&x.v>4);
    const v=c&&c.vehicles&&c.vehicles[0], bg=v&&v.bogies&&v.bogies[0];
    if(bg&&bg.wheelMesh&&Number.isFinite(v.wheelAngle)){
      bg.wheelMesh.getMatrixAt(bg.wheelIdx[0], m);
      const centre=new THREE.Vector3().setFromMatrixPosition(m);
      const top=new THREE.Vector3(0, v.wheelR, 0).applyMatrix4(m);
      if(prevC){
        const dC=centre.clone().sub(prevC);
        if(dC.length()>1e-4){
          const dir=dC.clone().normalize();
          got.push(top.clone().sub(prevT).dot(dir)/dC.length());
        }
      }
      prevC=centre.clone(); prevT=top.clone();
    }
    if(++n<200 && got.length<40) requestAnimationFrame(tick);
    else{ got.sort((a,b)=>a-b);
      const med=got[got.length>>1];
      res({samples:got.length, topSpeedVsCentre:+med.toFixed(2),
           verdict: med>1.4 ? 'FORWARD — correct (top runs ~2x centre)'
                  : med<0.6 ? 'BACKWARD or stationary — still wrong' : 'ambiguous'});}
  };
  requestAnimationFrame(tick);
})),null,1));
await b.close();
