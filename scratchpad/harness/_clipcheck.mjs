import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=far&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(14000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t = window.__lemWorld.subsystems.get('terrain');
  const r = window.__lemWorld.subsystems.get('rail');
  const E = t._ework;
  const spans = (r && (r.earthworks || r._earthworks || r.publishedEarthworks)) || null;
  const kinds = {};
  let list = null;
  try { list = r.earthworks && (typeof r.earthworks==='function'? r.earthworks(): r.earthworks); } catch(e){}
  const src = Array.isArray(list)? list : (Array.isArray(spans)? spans : null);
  if (src) for (const s of src) kinds[s.kind]=(kinds[s.kind]||0)+1;
  return {segments: E&&E.segments, clipped: E&&E.clipped, hasEc: !!(E&&E.ec),
          spanKinds: kinds, nSpans: src? src.length : null,
          railKeys: r? Object.keys(r).filter(k=>/ework|earth|struct|abut/i.test(k)) : null};
}),null,1));
await b.close();
