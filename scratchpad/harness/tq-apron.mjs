/* tq-apron.mjs — is there any relief on the coastal apron, and what is stopping
 * it? Walks radial transects from the waterline inland, reports the profile's
 * roughness (mean |second difference| at a 20 m step) and, at each station, the
 * distance to the nearest earthwork feature — which is the gate the dune field
 * is behind. A flat transect with a small `works` number means the gate. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:900,height:500}});
const mods = process.argv.includes('--rail') ? 'sky,gi,terrain,buildings,rail' : 'terrain';
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=wide&time=9&hud=0&quality=ultra`,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain');
  const out={waterY:+t.waterY.toFixed(1), islandR:t.islandR, transects:[]};
  let rough=0, n=0, worstStep=0, minWorks=1e9, meanWorks=0, nw=0;
  for(let k=0;k<24;k++){
    const a=k/24*Math.PI*2, cs=Math.cos(a), sn=Math.sin(a);
    // find the waterline on this bearing
    let lo=0, hi=(t.islandR||480)+600;
    for(let i=0;i<30;i++){const m=(lo+hi)/2; const h=t.heightAt(t.cx+cs*m,t.cz+sn*m); if(isFinite(h)&&h>t.waterY) lo=m; else hi=m;}
    const R=lo, prof=[], works=[];
    for(let s=0;s<=300;s+=20){
      const r=R-s, x=t.cx+cs*r, z=t.cz+sn*r;
      prof.push(+(t.heightAt(x,z)-t.waterY).toFixed(2));
      works.push(Math.round(t._distances?t._distances(x,z,null):-1));
    }
    let rr=0;
    for(let i=1;i+1<prof.length;i++){
      rr+=Math.abs(prof[i-1]-2*prof[i]+prof[i+1]);
      worstStep=Math.max(worstStep,Math.abs(prof[i]-prof[i-1]));
    }
    rr/=Math.max(1,prof.length-2); rough+=rr; n++;
    for(const v of works){ if(v>=0){minWorks=Math.min(minWorks,v); meanWorks+=v; nw++;} }
    if(k%6===0) out.transects.push({bearingDeg:Math.round(a*180/Math.PI), R:+R.toFixed(0), prof, works});
  }
  out.apronRoughnessM=+(rough/n).toFixed(2);
  out.worstStepM=+worstStep.toFixed(1);
  out.worksDistMin=minWorks<1e8?minWorks:null;
  out.worksDistMean=nw?Math.round(meanWorks/nw):null;
  // finer plan silhouette
  const NB=288, radii=[];
  for(let k=0;k<NB;k++){
    const a=k/NB*Math.PI*2; let lo=0,hi=(t.islandR||480)+600;
    for(let i=0;i<28;i++){const m=(lo+hi)/2; const h=t.heightAt(t.cx+Math.cos(a)*m,t.cz+Math.sin(a)*m); if(isFinite(h)&&h>t.waterY) lo=m; else hi=m;}
    radii.push(lo);
  }
  const mean=radii.reduce((s,v)=>s+v,0)/NB;
  let sig=0,curv=0;
  for(let k=0;k<NB;k++){sig+=(radii[k]-mean)**2; curv+=Math.abs(radii[(k+NB-1)%NB]-2*radii[k]+radii[(k+1)%NB]);}
  out.radiusMean=+mean.toFixed(1);
  out.radiusSigma=+Math.sqrt(sig/NB).toFixed(1);
  out.outlineRoughness288=+(curv/NB).toFixed(2);
  return out;
}),null,1));
await b.close();
