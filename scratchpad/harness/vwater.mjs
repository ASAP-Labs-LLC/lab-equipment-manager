import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation'), t = w.subsystems.get('terrain');
  // lowest ground under any live grass instance and any clutter instance
  const out = {waterY: t?.waterY, waterLevel: v.waterLevel, hMin: v.hMin, hMax: v.hMax};
  let lowG = 1e9, nG = 0;
  const G = v.grass;
  for (let i=0;i<G.count;i++){ const y = G.mats[i*16+13]; if (y<lowG) lowG=y; if (y < (v.waterLevel||-1e9)) nG++; }
  out.grassCount = G.count; out.grassMinY = lowG; out.grassBelowWater = nG;
  let lowC = 1e9, nC = 0, tot=0;
  for (const c of v.clutter){ for (let i=0;i<c.count;i++){ tot++; const y=c.mats[i*16+13]; if(y<lowC)lowC=y; if (y < (v.waterLevel||-1e9)) nC++; } }
  out.clutterCount = tot; out.clutterMinY = lowC; out.clutterBelowWater = nC;
  let lowT=1e9, nT=0, totT=0;
  for (const e of v.trees){ for (let i=0;i<e.list.length;i++){ totT++; const y=e.mats[i*16+13]; if(y<lowT)lowT=y; if (y<(v.waterLevel||-1e9)) nT++; } }
  out.treeCount=totT; out.treeMinY=lowT; out.treeBelowWater=nT;
  return out;
});
console.log(JSON.stringify(r,null,1));
await b.close();
