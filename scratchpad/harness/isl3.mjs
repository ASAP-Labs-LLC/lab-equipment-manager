import {chromium} from 'playwright';
const url=process.argv[2]||'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&quality=ultra&time=16&weather=clear&cam=wide';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(url,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
console.log(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  let rad=0, radKind='', ax=0;
  for(const f of t.features||[]){
    const pad=(f.t===0?Math.max(f.hx,f.hz):f.r)||0;
    const pts = f.t===0?[[f.cx,f.cz]]:[[f.ax,f.az],[f.bx,f.bz]];
    for(const [x,z] of pts){
      const r=Math.hypot(x-t.cx,z-t.cz)+pad;
      if(r>rad){rad=r; radKind=(f.kind||('t'+f.t))+' @'+x.toFixed(0)+','+z.toFixed(0)+' pad'+pad.toFixed(0);}
      ax=Math.max(ax, Math.abs(x-t.cx)+pad, Math.abs(z-t.cz)+pad);
    }
  }
  const kinds={};
  for(const f of t.features||[]) kinds[f.kind||('t'+f.t)]=(kinds[f.kind||('t'+f.t)]||0)+1;
  return JSON.stringify({nFeatures:(t.features||[]).length, kinds, radialReach:+rad.toFixed(1), worst:radKind, axisReach:+ax.toFixed(1), siteReach:t.siteReach},null,1);
}));
await b.close();
