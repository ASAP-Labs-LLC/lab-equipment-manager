/* Does the terrain mesh have real normals, and does its material light them?
 * The critic says the ground shows no directional response while objects on it
 * cast real shadows. Two candidate causes, and they are distinguishable:
 *   1. the normals are all +Y (a carved mesh whose normals were never recomputed)
 *   2. the normals vary but the material is not doing N.L */
import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld; const out={};
  w.scene.traverse(o=>{
    if(!o.isMesh || !/terrain-core/.test(o.name||'')) return;
    const n=o.geometry.attributes.normal; const N=n.count;
    let up=0, tilt=[], sum=0;
    for(let i=0;i<N;i+=Math.max(1,(N/4000)|0)){
      const y=n.getY(i); sum++;
      if(y>0.999) up++;
      tilt.push(Math.acos(Math.min(1,Math.max(-1,y)))*180/Math.PI);
    }
    tilt.sort((a,b)=>a-b);
    const m=o.material;
    out.mesh={name:o.name, verts:N, sampled:sum,
      pctNormalsDeadUp:+(100*up/sum).toFixed(1),
      tiltDeg:{p50:+tilt[tilt.length>>1].toFixed(1), p90:+tilt[(tilt.length*9/10)|0].toFixed(1), max:+tilt[tilt.length-1].toFixed(1)}};
    out.material={type:m.type, lights:m.lights, flatShading:m.flatShading,
      roughness:m.roughness, metalness:m.metalness,
      emissiveIntensity:m.emissiveIntensity,
      hasOnBeforeCompile:!!m.onBeforeCompile,
      defines:m.defines?Object.keys(m.defines):null};
  });
  return out;
}), null, 1));
await b.close();
