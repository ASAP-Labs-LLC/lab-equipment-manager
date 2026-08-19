import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1400,height:800}});
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,140)));
await p.goto('http://127.0.0.1:5601/floor',{waitUntil:'load'});
await p.waitForFunction(()=>window.__lemWorld && window.__lemWorld.plan, null, {timeout:60000});
await p.waitForTimeout(3000);
// Simulate the operator being signed in with the map unlocked.
const before = await p.evaluate(() => {
  window.AUTHED = true;
  const w = window.__lemWorld;
  w.setLocked(false);
  const st = w.plan.stations[0];
  return {uid: st.uid, gx: st.gx, gy: st.gy, canDrag: w.opts.canDrag ? w.opts.canDrag() : null,
          locked: w.locked, screen: w.screenPoint(st.uid, 6)};
});
console.log('before:', JSON.stringify(before));
if (before.screen) {
  await p.mouse.move(before.screen.x, before.screen.y);
  await p.mouse.down();
  for (let i=1;i<=10;i++) { await p.mouse.move(before.screen.x + i*14, before.screen.y + i*7); await p.waitForTimeout(30); }
  await p.mouse.up();
  await p.waitForTimeout(2500);
}
console.log('after :', JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld; const st = w.plan.byUid.get(w.plan.stations[0].uid);
  return {uid: st.uid, gx: st.gx, gy: st.gy};
})));
console.log('errors:', errs.slice(0,3));
await b.close();
