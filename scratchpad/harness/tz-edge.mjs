/* tz-edge.mjs — the soak's edge walk, over the soak's own layouts, with the
 * worst step on EVERY bearing reported rather than the first failing one.
 * Same bearings, same 20m step, same 26m rule. Terrain + rail only, so it is a
 * ~40s loop rather than the soak's several minutes.
 *
 *   node tz-edge.mjs [--layouts 6]
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const LAYOUTS = parseInt(args.layouts || '6', 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
function layouts(n) {
  const BAY = 2.05;
  const out = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];
  const all = [out];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4;
    const pos = [];
    for (let i = 0; i < FLEET.length; i++) {
      if (kind === 0) pos.push([Math.round(rnd() * 8) * BAY, Math.round(rnd() * 8) * BAY]);
      else if (kind === 1) pos.push([i * BAY, 0]);
      else if (kind === 2) pos.push([0, i * BAY]);
      else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]);
    }
    if (kind === 3) pos[1] = pos[0].slice();
    all.push(pos);
  }
  return all;
}

const WALK = () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain'), plan = w.plan;
  if (!t || !t.heightAt) return {error: 'no terrain'};
  const cx = (plan.bounds.minX + plan.bounds.maxX) / 2;
  const cz = (plan.bounds.minZ + plan.bounds.maxZ) / 2;
  const bearings = [];
  let worst = 0, worstAt = null, faults = 0;
  for (let b = 0; b < 8; b++) {
    const a = (b / 8) * Math.PI * 2;
    let prev = t.heightAt(cx, cz), bw = 0, bwr = 0, firstFault = null;
    for (let r = 20; r < 4000; r += 20) {
      const x = cx + Math.cos(a) * r, z = cz + Math.sin(a) * r;
      const h = t.heightAt(x, z);
      if (!isFinite(h)) { firstFault = {r, detail: 'non-finite'}; break; }
      const d = Math.abs(h - prev);
      if (d > bw) { bw = d; bwr = r; }
      if (d > 26 && !firstFault) {
        firstFault = {r, from: +prev.toFixed(1), to: +h.toFixed(1)};
      }
      prev = h;
    }
    if (firstFault) faults++;
    bearings.push({b, worstStep: +bw.toFixed(1), at: bwr, fault: firstFault});
    if (bw > worst) { worst = bw; worstAt = {b, r: bwr}; }
  }
  /* How much `sd` moves per metre of ground — the factor that turns a bounded
   * profile gradient into an unbounded one if it is large. */
  let gmax = 0;
  if (t._islandSD) {
    for (let b = 0; b < 24; b++) {
      const a = (b / 24) * Math.PI * 2;
      for (let r = 200; r < 1400; r += 20) {
        const x = cx + Math.cos(a) * r, z = cz + Math.sin(a) * r;
        const s0 = t._islandSD(x, z);
        if (Math.abs(s0) > 260) continue;
        const gx = (t._islandSD(x + 2, z) - t._islandSD(x - 2, z)) / 4;
        const gz = (t._islandSD(x, z + 2) - t._islandSD(x, z - 2)) / 4;
        const g = Math.hypot(gx, gz);
        if (g > gmax) gmax = g;
      }
    }
  }
  return {faults, worstStep: +worst.toFixed(1), worstAt,
          gradSDmax: +gmax.toFixed(2),
          islandR: Math.round(t.islandR), waterY: +t.waterY.toFixed(1),
          bearings: bearings.filter(x => x.fault || x.worstStep > 18)};
};

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
            '?mods=sky,gi,terrain,rail&cam=wide&time=13&hud=0';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 900, height: 500}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

const SET = layouts(LAYOUTS);
let total = 0;
for (let L = 0; L < SET.length; L++) {
  const ok = await page.evaluate(([fleet, pos]) => {
    const list = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tz',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    try { window.__lemWorld.setMachines(list); return true; }
    catch (e) { return 'setMachines threw: ' + e.message; }
  }, [FLEET, SET[L]]);
  if (ok !== true) { console.log(`L${L}: ${ok}`); continue; }
  await page.waitForTimeout(3500);
  const r = await page.evaluate(WALK);
  total += r.faults || 0;
  console.log(`L${L}`, JSON.stringify(r));
}
console.log('TOTAL edge faults:', total);
if (errs.length) console.log('console/page errors:', [...new Set(errs)].slice(0, 6));
await browser.close();
