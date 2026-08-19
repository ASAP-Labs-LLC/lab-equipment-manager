import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.evaluate(() => {
  window.__seen = {}; window.__trans = [];
  const T = window.__lemWorld.subsystems.get('trains');
  const prev = new Map();
  const tick = () => {
    for (const c of T.consists) {
      if (!c) continue;
      window.__seen[c.state] = (window.__seen[c.state]||0)+1;
      const was = prev.get(c.slot);
      if (was !== undefined && was !== c.state && window.__trans.length < 40)
        window.__trans.push(`${c.slot}: ${was} -> ${c.state}`);
      prev.set(c.slot, c.state);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  for (let i=0;i<12;i++) setTimeout(()=>window.__lemWorld.parse(['multitek-ns','optimpp-1','pac-flash-1'][i%3],'L'), i*200);
});
await p.waitForTimeout(45000);
console.log(JSON.stringify(await p.evaluate(() => ({
  statesSeen: window.__seen, transitions: window.__trans.slice(0,24)
})), null, 1));
await b.close();
