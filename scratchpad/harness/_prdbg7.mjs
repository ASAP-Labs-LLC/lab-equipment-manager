import {chromium} from 'playwright';
const MODS='sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b=await chromium.launch({headless:true,channel:'chromium',
 args:['--use-angle=metal','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods='+MODS+
 '&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(7000);
const r=await p.evaluate(()=>{
  const w=window.__lemWorld, pr=w.subsystems.get('props'), t=w.subsystems.get('terrain');
  const veg=w.subsystems.get('vegetation');
  const hasShore = typeof veg?._shore === 'function';
  const m=pr._mask; const rows=[];
  for(let j=0;j<m.N;j++)for(let i=0;i<m.N;i++){
    if(!m.land[j*m.N+i])continue;
    const x=m.x0+i*m.cell,z=m.z0+j*m.cell,dw=m.d[j*m.N+i]*m.cell;
    if(dw>pr._shoreW)continue;
    const s=t.biomeAt(x,z); if(!s||s.hard>0.25||s.kind==='hardstanding'||s.kind==='stream')continue;
    let sh=null; try{ sh=hasShore?veg._shore({coast:dw,x,z}):null; }catch(e){}
    rows.push({x,z,dw,a:s.altitude,sl:s.slope,forest:s.forest,
               beach:sh?sh.beach:null, bn:pr.beachnessAt(x,z,s)});
  }
  const above=rows.filter(r=>r.a>=2.95);
  const bins={};
  for(const th of [0.15,0.3,0.5,0.7,0.9]) bins['beach>'+th]=above.filter(r=>r.beach>th).length;
  const both={};
  for(const th of [0.15,0.3,0.5]) both['above+beach>'+th+'+bn>=.28']=
      above.filter(r=>r.beach>th&&r.bn>=0.28).length;
  const fq=v=>{const s=v.slice().sort((a,b)=>a-b);
    return s.length?[s[0],s[(s.length*0.25)|0],s[(s.length*0.5)|0],s[(s.length*0.75)|0],s[s.length-1]].map(x=>+x.toFixed(3)):null;};
  // and the current 10 sites
  const cur=(pr.umbrellaSites||[]).map(s=>{
    const dw=pr.dWaterAt(s.x,s.z); let sh=null;
    try{sh=hasShore?veg._shore({coast:dw,x:s.x,z:s.z}):null;}catch(e){}
    const bio=t.biomeAt(s.x,s.z);
    return {x:s.x,z:s.z,alt:s.altitude,dw:+dw.toFixed(0),
            beach:sh?+sh.beach.toFixed(2):null, forest:+bio.forest.toFixed(2)};
  });
  return {hasShore, bandCells:rows.length, aboveWash:above.length, bins, both,
    aboveDw:fq(above.map(r=>r.dw)), aboveBeach:fq(above.map(r=>r.beach).filter(v=>v!=null)),
    aboveForest:fq(above.map(r=>r.forest)),
    allBeachQ:fq(rows.map(r=>r.beach).filter(v=>v!=null)),
    currentSites:cur};
});
console.log(JSON.stringify(r,null,1));
await b.close();
