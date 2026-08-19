import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:480}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.evaluate(() => {
  window.__ev = [];
  const w = window.__lemWorld;
  for (const n of ['rail:earthworks','terrain:regraded','ready'])
    w.on(n, () => window.__ev.push([n, Math.round(performance.now())]));
});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(() => window.__ev)));
await b.close();
