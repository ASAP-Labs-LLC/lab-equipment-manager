import {chromium} from 'playwright';
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport:{width:1600,height:900}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods='+MODS+
 '&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(7000);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  let d=null, u=null; pr.group.traverse(o=>{ if(o.name==='props:decals') d=o;
                                             if(o.name==='props:umbrellas') u=o; });
  const g = d.geometry, pos = g.attributes.position;
  const bs = g.boundingSphere;
  // sample a few vertices and the colour attribute range
  const c = g.attributes.color;
  let cmin=9, cmax=-9;
  for(let i=0;i<c.count;i++){ cmin=Math.min(cmin,c.getX(i)); cmax=Math.max(cmax,c.getX(i)); }
  const rend = w.renderer || w._renderer || w.engine?.renderer;
  const before = rend ? rend.info.render.calls : null;
  return {
    verts: pos.count, tris: g.index.count/3,
    bs: bs? {c:[+bs.center.x.toFixed(1),+bs.center.y.toFixed(1),+bs.center.z.toFixed(1)],
             r:+bs.radius.toFixed(1)} : null,
    v0: [pos.getX(0),pos.getY(0),pos.getZ(0)],
    colRange:[+cmin.toFixed(3),+cmax.toFixed(3)],
    parentVisible: d.parent?.visible, groupVisible: pr.group.visible,
    layersDecal: d.layers.mask, layersUmb: u?u.layers.mask:null,
    camLayers: (w.camera||w.cam)?.layers?.mask,
    matSide: d.material.side, depthTest: d.material.depthTest,
    opacity: d.material.opacity, alphaTest: d.material.alphaTest,
    visible: d.visible, calls: before,
    rendererFound: !!rend,
  };
});
console.log(JSON.stringify(r,null,1));
await b.close();
