/* bx-face.mjs — the batter ANGLE THIS FILE ACTUALLY SHIPS, not the one it
 * intended, plus what happens to the schedule across a re-plan.
 *
 *  1. walk `_benchLevelAt` at 5 cm and report the steepest gradient on each
 *     riser after the smin fillets have had their say;
 *  2. walk `_designAt` down the middle of the site at 25 cm, which is the same
 *     thing with the mask and the plane in it;
 *  3. drive a re-plan through `setMachines` and check the benches follow, that
 *     the stale first emission is dropped, and that nothing re-grades twice.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const faces = t._terrace.risers.map(r => {
    const zc = r.z0 + r.run * 0.5;
    let best = 0, plateauErr = 0;
    for (let z = zc - 30; z <= zc + 30; z += 0.05) {
      const d = Math.abs(t._benchLevelAt(z + 0.05) - t._benchLevelAt(z - 0.05)) / 0.1;
      if (d > best) best = d;
    }
    /* and the plateaus: how far off its published level is the field 12 m
     * clear of the riser, which is where a pad may start */
    for (const z of [zc - r.run / 2 - 12, zc + r.run / 2 + 12]) {
      const lv = t._benchLevelAt(z);
      const band = t._terrace.bands.reduce((a, bb) =>
        Math.abs(bb.level - lv) < Math.abs(a.level - lv) ? bb : a);
      plateauErr = Math.max(plateauErr, Math.abs(lv - band.level));
    }
    return {nominalDeg: r2(Math.atan(Math.abs(r.rise) / r.run) * 180 / Math.PI),
            shippedDeg: r2(Math.atan(best) * 180 / Math.PI),
            shippedPct: r2(best * 100),
            plateauErrM: +plateauErr.toFixed(4)};
  });
  /* the design surface itself, down x = site centre */
  let dBest = 0, dz = 0;
  for (let z = -280; z <= 160; z += 0.25) {
    const d = Math.abs(t._designAt(t.cx, z + 0.25) - t._designAt(t.cx, z - 0.25)) / 0.5;
    if (d > dBest) { dBest = d; dz = z; }
  }
  /* every pad, exactly on its own level? */
  const pads = (w.plan.stations || []).map(s => ({
    uid: s.uid, bench: s.bench,
    designY: r2(t._designAt(s.x, s.z)),
    wantY: r2(3.0 + ((w.ctx.siteBenches.benches.find(bb => bb.id === s.bench) || {}).level)),
  }));
  return {faces,
          designMaxDeg: r2(Math.atan(dBest) * 180 / Math.PI), designMaxAtZ: r2(dz),
          pads, benchPasses: t._benchPasses || 0,
          benchGeom: t._benchGeom, ewPasses: t._ewPasses || 0};
});

/* --- a re-plan, driven through the same API the floor uses ---------------- */
const after = await p.evaluate(async () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const seen = [];
  w.ctx.on('site:benches', pl => seen.push((pl.benches || []).map(b => b.level == null
    ? 'null' : b.level.toFixed(3)).join(',')));
  const before = {geom: t._benchGeom, key: t._benchKey, passes: t._benchPasses || 0};
  /* move one machine two bays: a genuinely different layout */
  const list = w.machines.map(m => ({...m, pos: (m.pos || []).slice()}));
  if (list.length) list[0].pos = [(list[0].pos?.[0] ?? 0) + 3, (list[0].pos?.[1] ?? 0) + 2];
  w.setMachines(list);
  await new Promise(r => setTimeout(r, 2500));
  const sb = w.ctx.siteBenches;
  return {
    emissions: seen,
    geomChanged: before.geom !== t._benchGeom,
    keyChanged: before.key !== t._benchKey,
    benchPassesAfter: t._benchPasses || 0,
    terraceOnNewPlan: !!t._terrace,
    levelsAgree: (sb.benches || []).every(bb => {
      const band = (t._terrace ? t._terrace.bands : []).find(x => x.id === bb.id);
      return band && Math.abs(band.level - bb.level) < 1e-9;
    }),
    benchesNow: (sb.benches || []).map(bb => ({id: bb.id, level: r2(bb.level),
      designY: r2(t._designAt(bb.cx, bb.cz)), wantY: r2(3.0 + bb.level)})),
  };
});
console.log(JSON.stringify({...out, replan: after, pageErrors: errs.slice(0, 8)}, null, 1));
await b.close();
