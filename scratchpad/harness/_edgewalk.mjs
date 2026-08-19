/* The soak's own edge walk, reproduced with the sea in view — so "every fault
 * is the coast" is a measurement rather than a claim. Same bearings, same 20m
 * step, same 26m threshold. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&cam=wide&time=16',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain'), plan=w.plan;
  const cx=(plan.bounds.minX+plan.bounds.maxX)/2, cz=(plan.bounds.minZ+plan.bounds.maxZ)/2;
  const out=[];
  for(let bi=0;bi<8;bi++){
    const a=(bi/8)*Math.PI*2; let prev=t.heightAt(cx,cz);
    for(let r=20;r<4000;r+=20){
      const x=cx+Math.cos(a)*r, z=cz+Math.sin(a)*r; const h=t.heightAt(x,z);
      if(Math.abs(h-prev)>26){
        out.push({bearing:bi, r, from:+prev.toFixed(1), to:+h.toFixed(1),
          waterY:+t.waterY.toFixed(1),
          lowerAtOrBelowSea: h <= t.waterY + 0.5,
          upperIsDryLand: prev > t.waterY + 0.5,
          coastRadiusHere: Math.round(t.landRadiusAt(x,z)),
          kindAtLower: t.biomeAt(x,z).kind});
        break;
      }
      prev=h;
    }
  }
  return {waterY:+t.waterY.toFixed(1), islandR:Math.round(t.islandR),
          coastRMin:Math.round(t.coastRMin), coastRMean:Math.round(t.coastRMean),
          faults:out};
}),null,1));
await b.close();
