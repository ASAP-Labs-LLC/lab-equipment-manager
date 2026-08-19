import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:800,height:450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const r2=v=>+(+v).toFixed(2);
  const mesh=t.meshes.find(m=>m.name==='terrain-core');
  const g=mesh.geometry, pos=g.getAttribute('position'), X=g.getAttribute('aux'), nor=g.getAttribute('normal');
  // histogram of shore mask over land vertices, and over site vertices
  const q=new Float32Array(4);
  let land=0, siteN=0, siteShore=0, siteSD=0, landShore=0;
  const bins=new Array(11).fill(0);
  const sdbins={};
  for(let i=0;i<pos.count;i++){
    const x=pos.getX(i),y=pos.getY(i),z=pos.getZ(i);
    const aw=y-t.waterY;
    if(aw<0.05) continue;
    land++; const s=X.getW(i); landShore+=s;
    bins[Math.min(10,Math.floor(s*10))]++;
    t._distances(x,z,q); const dF=Math.min(q[0],t._railDist(x,z));
    if(dF<120){ siteN++; siteShore+=s; siteSD+=t._islandSD(x,z); }
  }
  // sample _islandSD at the bench centres
  const pts=[[t.cx,-82],[t.cx,0],[t.cx,45],[t.cx,-185],[0,0],[350,0]];
  return {waterY:r2(t.waterY), yShift:r2(t.yShift), islandR:r2(t.islandR),
    coastRMin:r2(t.coastRMin), coastRMean:r2(t.coastRMean),
    landVerts:land, meanShoreLand:r2(landShore/land),
    shoreHist:bins, siteVerts:siteN, meanShoreSite:r2(siteShore/siteN),
    meanIslandSDSite:r2(siteSD/siteN),
    probes:pts.map(([x,z])=>({x,z,h:r2(t.heightAt(x,z)),aw:r2(t.heightAt(x,z)-t.waterY),sd:r2(t._islandSD(x,z))})),
    SITE_Y_designAt:r2(t._designAt(t.cx,t.cz)),
  };
})));
await b.close();
