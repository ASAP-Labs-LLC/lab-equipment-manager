/* The sward, ablated in place: two frames from one session with only the sward
 * meshes' visibility changed between them. The scene totals are moving under
 * this round — other builders are live in terrain, gi and rail, and two shots
 * ten minutes apart differ in lighting, draw calls and a third of a million
 * triangles — so a before/after taken from disk proves nothing. This is the
 * only comparison that is about this tier. */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'top');
const quality = arg('quality', 'ultra');
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errs.push(m.text().slice(0, 200)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 200)));
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${cam}&time=16&hud=0&quality=${quality}`, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6500);
const set = async on => p.evaluate(v => {
  const W = window.__lemWorld, s = W.subsystems.get('vegetation');
  for (const x of (s.sward || [])) x.mesh.visible = v;
  W.engine.shadowNeedsUpdate = true;
}, on);
/* Ten samples and the median of each, not one frame. A single read of
 * `stats()` caught the scene mid-transition once and reported the sward as 159
 * draw calls — the adaptive ladder and three other builders are live, and any
 * one-frame difference is mostly theirs. */
const stat = async () => {
  const s = [];
  for (let i = 0; i < 10; i++) { await p.waitForTimeout(260);
    s.push(await p.evaluate(() => {
      const v = window.__lemWorld.subsystems.get('vegetation');
      let placed = 0, drawn = 0;
      for (const x of (v.sward || [])) { placed += x.count; drawn += x.mesh.count; }
      return {placed, drawn, ...window.__lemWorld.stats()};
    })); }
  const med = k => s.map(x => x[k]).sort((a, c) => a - c)[5];
  return {placed: s[0].placed, drawn: s[0].drawn, tier: s[0].tier,
          drawCalls: med('drawCalls'), triangles: med('triangles'),
          spreadDraws: Math.max(...s.map(x => x.drawCalls)) - Math.min(...s.map(x => x.drawCalls))};
};
await set(true); await p.waitForTimeout(1200);
const on = await stat();
await p.screenshot({path: `../shots/swab-${cam}-on.png`});
await set(false); await p.waitForTimeout(1200);
const off = await stat();
await p.screenshot({path: `../shots/swab-${cam}-off.png`});
await set(true);
await b.close();
console.log(JSON.stringify({cam, quality, on, off,
  cost: {draws: on.drawCalls - off.drawCalls, tris: on.triangles - off.triangles}, errs}, null, 1));
