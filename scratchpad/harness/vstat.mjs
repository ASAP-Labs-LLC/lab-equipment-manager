import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
const o = await p.evaluate(()=>{
  const W=window.__lemWorld, v=W.subsystems.get('vegetation'), t=W.subsystems.get('terrain');
  const out={stats:v._scatterStats, island:v.island, groveR:v.groveR, land:v._land, area:v._area?v._area(v.plan):null,
    treeBudget:v._treeBudget, quality:v.quality, range:v.range, relief:v.relief, waterLevel:v.waterLevel,
    terr:{islandR:t?.islandR, coastWobble:t?.coastWobble, waterY:t?.waterY, cx:t?.cx, cz:t?.cz, ringSize:t?.ringSize},
    groveStats:v._groveStats, clutterStats:v._clutterStats,
    counts:{trees:(v.trees||[]).reduce((a,e)=>a+e.list.length,0), groves:(v.groves||[]).reduce((a,g)=>a+g.count,0)},
    grassCap: v.grass? v.grass.capacity : null,
  };
  // sample biome kinds over the island
  const isl=v.island; const kinds={}; let n=0, dry=0;
  for(let i=0;i<4000;i++){
    const a=Math.random()*6.283, r=Math.sqrt(Math.random())*isl.r;
    const x=isl.cx+Math.cos(a)*r, z=isl.cz+Math.sin(a)*r;
    let k='?'; try{k=t.biomeAt(x,z).kind;}catch{}
    kinds[k]=(kinds[k]||0)+1; n++;
    if((t.heightAt?t.heightAt(x,z):0)>v.waterLevel) dry++;
  }
  out.kinds=kinds; out.dryFrac=dry/n;
  return out;
});
console.log(JSON.stringify(o,null,1));
await b.close();
