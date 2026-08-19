import {chromium} from 'playwright';
const MODS='sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b=await chromium.launch({headless:true,channel:'chromium',
 args:['--use-angle=metal','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods='+MODS+
 '&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(7000);
const r=await p.evaluate(async ()=>{
  const w=window.__lemWorld; const pr=w.subsystems.get('props');
  let d=null; pr.group.traverse(o=>{if(o.name==='props:decals')d=o;});
  // is it reachable from the scene root?
  let inScene=false; w.scene.traverse(o=>{ if(o===d) inScene=true; });
  // chain of ancestors and their visibility
  const chain=[]; let n=d; while(n){ chain.push([n.name||n.type, n.visible,
      n.layers.mask, n.frustumCulled, !!n.matrixAutoUpdate]); n=n.parent; }
  // does hiding it change draw calls?
  const rend = w.renderer||w._renderer||w.engine?.renderer;
  const sample=async()=>{ await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
    return {calls:rend.info.render.calls, tris:rend.info.render.triangles}; };
  d.visible=true;  const on=await sample();
  d.visible=false; const off=await sample();
  d.visible=true;  const on2=await sample();
  // is the geometry actually indexed & non-degenerate?
  const g=d.geometry, pos=g.attributes.position;
  let nan=0; for(let i=0;i<pos.count;i++){ if(!Number.isFinite(pos.getX(i))||
      !Number.isFinite(pos.getY(i))||!Number.isFinite(pos.getZ(i))) nan++; }
  let imax=0; const ix=g.index; for(let i=0;i<ix.count;i++) imax=Math.max(imax,ix.getX(i));
  return {inScene, chain, on, off, on2, verts:pos.count, idxMax:imax, idxCount:ix.count,
     nanVerts:nan, bsR:g.boundingSphere?.radius,
     groups:g.groups, drawRange:g.drawRange,
     matVisible:d.material.visible, colorWrite:d.material.colorWrite,
     idxType:ix.array.constructor.name};
});
console.log(JSON.stringify(r,null,1));
await b.close();
