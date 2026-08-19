import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal']});
const p=await b.newPage({viewport:{width:800,height:450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&time=16',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
console.log(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const sd=[],flr=[],warp=[];
  for(let i=0;i<32;i++){
    const a=i*Math.PI/16;
    let lo=0,hi=3000;
    for(let k=0;k<40;k++){const m=(lo+hi)/2;
      if(t._islandSD(t.cx+Math.cos(a)*m,t.cz+Math.sin(a)*m)<0) lo=m; else hi=m;}
    sd.push(+lo.toFixed(0));
    flr.push(+t._coastFloorAt(Math.cos(a),Math.sin(a)).toFixed(0));
  }
  // raw warp magnitude at the mean coast radius
  const R=t.islandR;
  for(let i=0;i<32;i++){
    const a=i*Math.PI/16, x=t.cx+Math.cos(a)*R, z=t.cz+Math.sin(a)*R;
    warp.push(+(t._islandSD(x,z)).toFixed(1));
  }
  return JSON.stringify({islandR:+R.toFixed(0),A:+t._coastA.toFixed(1),C:+t._coastC.toFixed(1),
    f1:+(1/t._coastF1).toFixed(0),f2:+(1/t._coastF2).toFixed(0),
    coastMinRange:[Math.min(...t.coastMin),Math.max(...t.coastMin)].map(v=>+v.toFixed(0)),
    sdZero:sd, floor:flr, sdAtR:warp});
}));
await b.close();
