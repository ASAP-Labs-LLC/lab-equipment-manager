/* The sward, alone, over a checker of nothing else: is it drawing at all, and
 * what does one patch look like? Everything in the subsystem except the sward
 * meshes is hidden, terrain is left in so there is a floor to see it against. */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const dist = +arg('dist', '90');
const pitch = +arg('pitch', '0.5');
const only = arg('only', '1') === '1';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation&cam=wide&time=16&hud=0&quality=ultra', {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4500);
const info = await p.evaluate(({dist, pitch, only}) => {
  const W = window.__lemWorld, v = W.subsystems.get('vegetation'), r = W.rig;
  const sw = new Set(v.sward.map(s => s.mesh));
  if (only) for (const m of v.meshes) m.visible = sw.has(m);
  /* Stand over the densest sward, so the frame is the tier and not the sea. */
  let best = null;
  for (const s of v.sward) for (let i = 0; i < s.count; i += 3) {
    let n = 0;
    for (const t of v.sward) for (let j = 0; j < t.count; j += 7) {
      const dx = t.xs[j] - s.xs[i], dz = t.zs[j] - s.zs[i];
      if (dx * dx + dz * dz < 70 * 70) n++;
    }
    if (!best || n > best.n) best = {x: s.xs[i], z: s.zs[i], n};
  }
  r.maxDistance = Math.max(r.maxDistance || 0, 6000);
  r.goalTarget.set(best.x, v._ground(best.x, best.z), best.z);
  r.target.copy(r.goalTarget);
  r.goalDistance = dist; r.distance = dist;
  r.goalPitch = pitch; r.pitch = pitch; r.goalYaw = -0.7; r.yaw = -0.7;
  r.idleDrift = false;
  r.apply(1);
  v._repartition(true);
  let drawn = 0, placed = 0;
  for (const s of v.sward) { drawn += s.mesh.count; placed += s.count; }
  const m = v.matSward;
  return {best, drawn, placed, alphaTest: m.alphaTest, side: m.side,
          hasMap: !!m.map, visible: v.sward.every(s => s.mesh.visible)};
}, {dist, pitch, only});
await p.waitForTimeout(1500);
await p.screenshot({path: `../shots/swlook-${dist}-${only ? 'only' : 'all'}.png`});
await b.close();
console.log(JSON.stringify(info, null, 1));
