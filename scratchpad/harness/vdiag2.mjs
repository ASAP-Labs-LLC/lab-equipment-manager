import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2000);
const r = await p.evaluate(async ()=>{
  const THREE = await import('three');
  const w = window.__lemWorld, v = w.subsystems.get('vegetation'), t = w.subsystems.get('terrain');
  const cam = w.engine.camera;
  const out = {waterY: t?.waterY, yShift: t?.yShift, vegWaterLevel: v.waterLevel,
               hMin: v.hMin, hMax: v.hMax, relief: v.relief};
  // how much of the veg area is in the band [waterY, 0.6]
  const b2 = v._area(v.plan);
  let below=0, band=0, above=0, tot=0;
  for (let i=0;i<120;i++) for (let j=0;j<120;j++){
    const x = b2.x0+(b2.x1-b2.x0)*i/119, z = b2.z0+(b2.z1-b2.z0)*j/119;
    const h = v._ground(x,z); tot++;
    if (h < (t?.waterY ?? -8)) below++; else if (h < 0.6) band++; else above++;
  }
  out.areaFrac = {underWater:+(below/tot).toFixed(3), bankBand:+(band/tot).toFixed(3), dry:+(above/tot).toFixed(3)};
  // visible far tree distance histogram along the view
  const dir = new THREE.Vector3(); cam.getWorldDirection(dir);
  const hist = new Array(24).fill(0);
  for (const e of v.trees){ const a=e.far.instanceMatrix.array;
    for (let i=0;i<e.far.count;i++){
      const x=a[i*16+12], z=a[i*16+14];
      const d=Math.hypot(x-cam.position.x,z-cam.position.z);
      hist[Math.min(23,(d/50)|0)]++;
    }}
  out.farHist50m = hist;
  const hist2 = new Array(24).fill(0);
  for (const e of v.trees){ const a=e.near.instanceMatrix.array;
    for (let i=0;i<e.near.count;i++){
      const x=a[i*16+12], z=a[i*16+14];
      const d=Math.hypot(x-cam.position.x,z-cam.position.z);
      hist2[Math.min(23,(d/50)|0)]++;
    }}
  out.nearHist50m = hist2;
  // ground profile along the view ray with water surface
  const prof=[];
  for(let d=150; d<=1300; d+=25){
    const x=cam.position.x+dir.x*d, z=cam.position.z+dir.z*d;
    prof.push([d, +v._ground(x,z).toFixed(1)]);
  }
  out.profile = prof;
  return out;
});
console.log(JSON.stringify(r));
await b.close();
