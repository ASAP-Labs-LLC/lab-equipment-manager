import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('console', m => { if (m.type()==='error') console.log('CONSOLE ERR', m.text()); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3000);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const cam = w.ctx?.camera || w.camera;
  const out = {plan: {bounds: w.plan?.bounds, hub: w.plan?.hub && {x:w.plan.hub.x,z:w.plan.hub.z},
                      stations: (w.plan?.stations||[]).map(s=>({x:s.x,z:s.z}))},
               camPos: cam && {x:cam.position.x,y:cam.position.y,z:cam.position.z},
               target: w.rig?.target && {x:w.rig.target.x,z:w.rig.target.z},
               fog: w.scene.fog && {type:w.scene.fog.type||w.scene.fog.constructor.name, density:w.scene.fog.density, near:w.scene.fog.near, far:w.scene.fog.far},
               near:0, far:0, placed:0, grass: v?.grass?.count};
  if (!v) return out;
  // distance histogram of placed trees from camera
  const hist = new Array(16).fill(0);
  let minD = 1e9;
  for (const e of v.trees) {
    out.near += e.near.count; out.far += e.far.count; out.placed += e.list.length;
    for (let i=0;i<e.list.length;i++){
      const d = Math.hypot(e.xs[i]-cam.position.x, e.zs[i]-cam.position.z);
      if (d<minD) minD=d;
      hist[Math.min(15, Math.floor(d/100))]++;
    }
  }
  out.hist100m = hist; out.minTreeDist = Math.round(minD);
  // shader check
  const progs = w.engine.renderer.info.programs || [];
  out.programs = progs.map(pr=>pr.cacheKey).filter(k=>/lem-veg/.test(k));
  const gl = w.engine.renderer.getContext();
  const dumps = [];
  for (const pr of progs) {
    if (!/lem-veg/.test(pr.cacheKey||'')) continue;
    try {
      const shaders = gl.getAttachedShaders(pr.program.program || pr.program);
      for (const s of shaders) {
        const src = gl.getShaderSource(s);
        if (src.includes('void main')) dumps.push({key:pr.cacheKey, frag: src.includes('gl_FragColor')&&src.includes('vegPass'), sss: src.includes('vegPass'), fade: src.includes('vegVis'), colour: src.includes('vVegTint'), isFrag: src.includes('RE_Direct')});
      }
    } catch(e){ dumps.push({err:String(e)}); }
  }
  out.shader = dumps;
  return out;
});
console.log(JSON.stringify(r,null,1));
await b.close();
