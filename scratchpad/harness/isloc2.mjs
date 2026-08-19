import {chromium} from 'playwright';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:640,height:360}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1000);
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  const oc=t.meshes.find(m=>m.name==='terrain-ocean');
  let inScene=false, o=oc;
  const chain=[];
  while(o){chain.push(o.name||o.type); if(o===w.ctx.scene) inScene=true; o=o.parent;}
  return JSON.stringify({
    chain, inScene, visible:oc.visible, layers:oc.layers.mask, camLayers:w.ctx.camera.layers.mask,
    matVis: oc.material.visible, opacity: oc.material.opacity, colorWrite: oc.material.colorWrite,
    blending: oc.material.blending, side: oc.material.side, renderOrder: oc.renderOrder,
    drawRange: oc.geometry.drawRange, idxCount: oc.geometry.index.count,
    posY: oc.geometry.attributes.position.getY(0),
    matrixWorld: oc.matrixWorld.elements.slice(12,15),
    groupVisible: t.group.visible,
    parentVis: oc.parent && oc.parent.visible,
  });
}));
await b.close();
