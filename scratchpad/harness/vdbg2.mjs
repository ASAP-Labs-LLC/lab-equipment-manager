import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('console', m => { const t=m.text(); if(/vegetation|terrain|world\]/.test(t)) console.log('['+m.type()+']', t.slice(0,300)); });
p.on('pageerror', e => console.log('PAGEERR', String(e).slice(0,300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const W=window.__lemWorld, v=W.subsystems.get('vegetation'), t=W.subsystems.get('terrain');
  const Tex=v.ctx.Tex;
  const b=v._area(v.plan);
  const s=[];
  for(let i=0;i<6;i++){ const x=b.x0+(b.x1-b.x0)*(i+0.5)/6; for(let j=0;j<6;j++){ const z=b.z0+(b.z1-b.z0)*(j+0.5)/6;
    s.push({x:x|0,z:z|0,h:+v._ground(x,z).toFixed(1),stand:+(Tex.fbm(x*0.0018,z*0.0018,{octaves:3,period:8,seed:7})).toFixed(3), site: !!v._site(x,z)});}}
  return {ok:v.ok, buckets:v.trees.length, area:b, relief:+v.relief.toFixed(1), waterLevel:+v.waterLevel.toFixed(1), waterY:+v.waterY.toFixed(1), plantFloor:+v.plantFloor.toFixed(1), hMin:+v.hMin.toFixed(1), hMax:+v.hMax.toFixed(1), specShapes: (v.constructor, s.slice(0,10)), terrainKeys: t?Object.keys(t).filter(k=>/isl|coast|water|shore|sea/i.test(k)):null};
}),null,1));
await b.close();
