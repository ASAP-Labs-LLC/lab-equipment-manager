import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:900,height:520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const rail = window.__lemWorld.subsystems.get('rail');
  const out = {};
  for (const t of rail.tracks) {
    if (!t.frames) continue;
    const f=t.frames, n=f.count;
    const at = s => { const i=Math.max(0,Math.min(n-1,Math.round(s/f.step))); return [+f.pos[i*3].toFixed(1),+f.pos[i*3+1].toFixed(1),+f.pos[i*3+2].toFixed(1)];};
    out[t.name]={len:+t.length.toFixed(1), from:+(t.renderFrom||0).toFixed(1), to:+Math.min(t.renderTo,t.length).toFixed(1),
      p0:at(0), pFrom:at(t.renderFrom||0), pTo:at(Math.min(t.renderTo,t.length)), pEnd:at(t.length),
      bores:(t.bores||[]).map(x=>[+x[0].toFixed(1),+x[1].toFixed(1)]),
      decks:(t.decks||[]).map(x=>[+x[0].toFixed(1),+x[1].toFixed(1)])};
  }
  out._meta={hub:rail.hub&&[rail.hub.x,rail.hub.z], ringR:rail.ringR, waterY:rail.waterY,
    site:rail._site&&{...rail._site}, report:rail.earthworkReport&&rail.earthworkReport()};
  return out;
}),null,1));
await b.close();
