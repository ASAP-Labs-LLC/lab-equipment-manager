import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:900,height:500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=wide&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const out={hasFeatures:!!(t.features&&t.features.length), nFeatures:t.features?t.features.length:0, rows:[]};
  const a=Math.PI, cs=Math.cos(a), sn=Math.sin(a);
  let lo=0,hi=(t.islandR||480)+600;
  for(let i=0;i<30;i++){const m=(lo+hi)/2;const h=t.heightAt(t.cx+cs*m,t.cz+sn*m); if(isFinite(h)&&h>t.waterY) lo=m; else hi=m;}
  const R=lo;
  for(let s=0;s<=280;s+=10){
    const r=R-s, x=t.cx+cs*r, z=t.cz+sn*r;
    out.rows.push({s, sd:+t._islandSD(x,z).toFixed(1),
      raw:+t._rawHeight(x,z).toFixed(2), base:+t._baseHeight(x,z).toFixed(2),
      grad:+t.heightAt(x,z).toFixed(2), works:Math.round(t._distances(x,z,null))});
  }
  out.R=+R.toFixed(0); out.waterY=+t.waterY.toFixed(1); out.yShift=+(t.yShift||0).toFixed(2);
  out.beachW=+t.beachW.toFixed(0); out.cliffW=+t.cliffW.toFixed(0);
  return out;
}),null,1));
await b.close();
