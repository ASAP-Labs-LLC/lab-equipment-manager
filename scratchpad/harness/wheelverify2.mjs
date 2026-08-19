/* Find which local axis actually orbits, then judge the sense with that one. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1000,height:700}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(14000);
console.log(JSON.stringify(await p.evaluate(()=>new Promise(res=>{
  const THREE=window.__lemWorld.ctx.THREE, T=window.__lemWorld.subsystems.get('trains');
  const m=new THREE.Matrix4();
  const probes=[['localX',[1,0,0]],['localY',[0,1,0]],['localZ',[0,0,1]]];
  const prev={}, acc={}; let n=0;
  probes.forEach(([k])=>{acc[k]=[];});
  const tick=()=>{
    const c=(T.consists||[]).find(x=>x&&x.state==='out'&&x.v>4);
    const v=c&&c.vehicles&&c.vehicles[0], bg=v&&v.bogies&&v.bogies[0];
    if(bg&&bg.wheelMesh){
      bg.wheelMesh.getMatrixAt(bg.wheelIdx[0], m);
      const centre=new THREE.Vector3().setFromMatrixPosition(m);
      const cur={centre};
      probes.forEach(([k,a])=>{ cur[k]=new THREE.Vector3(a[0]*v.wheelR,a[1]*v.wheelR,a[2]*v.wheelR).applyMatrix4(m); });
      if(prev.centre){
        const dC=cur.centre.clone().sub(prev.centre);
        if(dC.length()>1e-4){
          const dir=dC.clone().normalize();
          probes.forEach(([k])=>{ acc[k].push(cur[k].clone().sub(prev[k]).dot(dir)/dC.length()); });
        }
      }
      Object.assign(prev, cur);
    }
    if(++n<220 && acc.localX.length<40) requestAnimationFrame(tick);
    else{
      const out={};
      probes.forEach(([k])=>{ const s=acc[k].slice().sort((a,b)=>a-b);
        out[k]= s.length? +s[s.length>>1].toFixed(2) : null; });
      // the orbiting axis is the one whose ratio departs from 1
      const spin = Object.entries(out).filter(([,v2])=>v2!==null && Math.abs(v2-1)>0.25);
      res({ratios:out, orbiting:spin.map(([k,v2])=>`${k}=${v2}`),
           verdict: spin.some(([,v2])=>v2>1.4) ? 'FORWARD — correct'
                  : spin.some(([,v2])=>v2<0.6) ? 'BACKWARD — still wrong'
                  : 'no axis orbits — wheels are not turning at all'});
    }
  };
  requestAnimationFrame(tick);
})),null,1));
await b.close();
