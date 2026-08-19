import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1000,height:700}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(14000);
console.log(JSON.stringify(await p.evaluate(()=>new Promise(res=>{
  const THREE=window.__lemWorld.ctx.THREE, T=window.__lemWorld.subsystems.get('trains');
  const m=new THREE.Matrix4(); const angles=[], mats=[]; let n=0;
  const tick=()=>{
    const c=(T.consists||[]).find(x=>x&&x.state==='out'&&x.v>4);
    const v=c&&c.vehicles&&c.vehicles[0], bg=v&&v.bogies&&v.bogies[0];
    if(bg&&bg.wheelMesh){
      angles.push(+v.wheelAngle.toFixed(3));
      bg.wheelMesh.getMatrixAt(bg.wheelIdx[0], m);
      mats.push(m.elements.slice(0,3).map(x=>+x.toFixed(3)).join(','));
    }
    if(++n<40) requestAnimationFrame(tick);
    else res({wheelAngleSamples:angles.slice(0,8),
              angleDelta:+(angles[7]-angles[0]).toFixed(3),
              basisXFirstFrames:mats.slice(0,4),
              basisXChanges: new Set(mats).size});
  };
  requestAnimationFrame(tick);
})),null,1));
await b.close();
