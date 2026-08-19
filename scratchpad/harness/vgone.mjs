import {chromium} from 'playwright';
const CAM=process.argv[2]||'far', W=+(process.argv[3]||1920), H=+(process.argv[4]||1080);
const URL=`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${CAM}&time=9&weather=clear&hud=0&quality=ultra`;
const b=await chromium.launch({headless:true,channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist','--disable-gpu-vsync','--disable-frame-rate-limit']});
const p=await (await b.newContext({viewport:{width:W,height:H}})).newPage();
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,140)));
const t0=Date.now();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
const ready=Date.now()-t0;
await p.waitForTimeout(4000);
const r=await p.evaluate(()=>{
  const veg=window.__lemWorld.subsystems.get('vegetation');
  const m=new Map(); let total=0;
  ((veg&&veg.group)||window.__lemWorld.scene).traverse(o=>{
    if(!o.isInstancedMesh)return;
    const g=o.geometry,i=g.index?g.index.count:g.attributes.position.count,per=i/3;
    m.set(per,(m.get(per)||0)+o.count); total+=per*o.count;});
  return {groves:(veg&&veg.groves?veg.groves.length:'n/a'),
          matGrove:!!(veg&&veg.matGrove), canopy:!!(veg&&veg.canopy),
          eightTri:m.get(8)||0, sixTri:m.get(6)||0, vegTris:Math.round(total)};});
const ms=await p.evaluate(()=>new Promise(res=>{const f=[];let l=performance.now();
  const s=l+3000;const t=n=>{f.push(n-l);l=n;n<s?requestAnimationFrame(t):
  (f.sort((a,b)=>a-b),res(f[f.length>>1]));};requestAnimationFrame(t);}));
const st=await p.evaluate(()=>window.__lemWorld.stats());
console.log(`${CAM} ${W}x${H}  grove meshes=${r.groves}  matGrove=${r.matGrove}  canopyAtlas=${r.canopy}`);
console.log(`  8-tri instances: ${r.eightTri}   6-tri (far cards): ${r.sixTri}   veg tris: ${r.vegTris.toLocaleString()}`);
console.log(`  frame ${ms.toFixed(2)} ms (${Math.round(1000/ms)} fps)  draws ${st.drawCalls}  scene tris ${st.triangles.toLocaleString()}  ready ${ready} ms`);
if(errs.length) console.log('  ERRORS:',errs.slice(0,2));
await b.close();
