/* A rolling wheel's TOP moves forward at 2x the vehicle speed and its CONTACT
 * point is instantaneously stationary. Sample a rim point off the real instance
 * matrix across frames and compare its motion with the vehicle's. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1000,height:700}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(12000);
console.log(JSON.stringify(await p.evaluate(()=>new Promise(res=>{
  const THREE=window.__lemWorld.ctx.THREE, T=window.__lemWorld.subsystems.get('trains');
  const m=new THREE.Matrix4(), topL=new THREE.Vector3(), prevTop=null;
  let prevPos=null, out=null, n=0;
  const tick=()=>{
    const c=(T.consists||[]).filter(x=>x&&x.state==='out'&&x.v>3)[0];
    if(c){
      const v=c.vehicles[0], bg=v&&v.bogies&&v.bogies[0];
      if(bg&&bg.wheelMesh){
        bg.wheelMesh.getMatrixAt(bg.wheelIdx[0], m);
        // world position of the wheel centre
        const centre=new THREE.Vector3().setFromMatrixPosition(m);
        // a point on the rim, taken through the same matrix: local +Y is "up"
        // in the wheel's own basis, so this is the top of the tyre
        topL.set(0, v.wheelR, 0).applyMatrix4(m);
        if(prevPos && prevTop){
          const dCentre=centre.clone().sub(prevPos);
          const dTop=topL.clone().sub(prevTop);
          // project both on the direction the centre is travelling
          const dir=dCentre.clone().normalize();
          const rateCentre=dCentre.length();
          const rateTop=dTop.dot(dir);
          out={centreStep:+rateCentre.toFixed(4), topStepAlongTravel:+rateTop.toFixed(4),
               ratio:+(rateTop/rateCentre).toFixed(2),
               expected:'+2.0 for a wheel rolling forward, -0.0 to negative if it spins backwards'};
        }
        prevPos=centre.clone(); prevTop=topL.clone();
      }
    }
    if(++n<90 || !out) requestAnimationFrame(tick); else res(out);
  };
  requestAnimationFrame(tick);
})),null,1));
await b.close();
