import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for(let run=1;run<=4;run++){
  const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
  const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,110)));
  await p.goto(URL,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  const series=[];
  for(const t of [0,600,1500,3000,6000]){
    await p.waitForTimeout(t===0?0:(t-series.at(-1).t));
    const n=await p.evaluate(()=>{let inst=0,meshes=0;
      window.__lemWorld.scene.traverse(o=>{if(o.isInstancedMesh){meshes++;inst+=o.count;}});
      return {inst,meshes};});
    series.push({t,...n});
  }
  console.log(`run ${run}  ` + series.map(s=>`${s.t}ms:${s.inst}`).join('  ') +
              `  meshes=${series.at(-1).meshes}` + (errs.length?`  ERR:${errs[0]}`:''));
  await p.context().close();
}
await b.close();
