import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:800,height:450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const e=t.eros; const st=t.erosStats;
  // what the carve ASKED for, per cell, on land
  let mx=0, n=0, sum=0; const vals=[];
  for(let k=0;k<e.flow.length;k++){
    const f=e.flow[k]; if(f<=0.20) continue;
    const c=3.4*Math.pow(f,1.6); vals.push(c); sum+=c; n++; if(c>mx)mx=c;
  }
  vals.sort((a,b)=>a-b);
  return {erosStats:st, cell:e.cs, N:e.N,
    carveAsked:{cellsOverFlow020:n, max:+mx.toFixed(2), mean:+(sum/n).toFixed(3),
      p50:+vals[vals.length>>1].toFixed(3), p90:+vals[Math.floor(vals.length*0.9)].toFixed(3)}};
})));
await b.close();
