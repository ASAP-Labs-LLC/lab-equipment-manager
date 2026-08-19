/* padshot.mjs — look at a station's ground on an AWKWARD layout.
 *
 *   node padshot.mjs --layout 7 --station 3 --out shot.png
 *
 * `solo.html` only ever builds the demo fleet, and the defect that matters here
 * only appears when the fleet is spread wider than the fine field — so this
 * applies one of the soak's layouts, points the camera at one station from
 * ground level, and also measures the pad: the terrain height on a ring around
 * the building against the height at its centre, which is what "the terrain
 * beneath them generates" actually means.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const LAYOUT = parseInt(args.layout || '7', 10);
const STATION = parseInt(args.station || '0', 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
function layouts(n) {
  const BAY = 2.05;
  const all = [[[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]]];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4, pos = [];
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

const browser = await chromium.launch({args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
await page.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${args.mods||'sky,gi,terrain,buildings,rail,vegetation'}&time=16&hud=0`,
                {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});

await page.evaluate(([fleet, pos]) => {
  window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
    machine_uid: uid, title, status, pos: pos[i], reason: 'padshot',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
}, [FLEET, layouts(LAYOUT + 1)[LAYOUT]]);
await page.waitForTimeout(4000);

const info = await page.evaluate(idx => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const st = w.plan.stations[idx];
  if (!st) return null;
  /* The pad, sampled as a ring rather than as a point. A dock height that
   * matches the terrain at the station's centre says nothing about whether the
   * ground on the far side of the building was ever cut. */
  const ring = [];
  for (let r of [10, 20, 27, 34]) {
    for (let a = 0; a < 12; a++) {
      const ang = (a / 12) * Math.PI * 2;
      ring.push(t.heightAt(st.x + Math.cos(ang) * r, st.z + Math.sin(ang) * r));
    }
  }
  const c = t.heightAt(st.x, st.z);
  const dev = ring.map(h => h - c);
  /* Park the camera at eye height, ninety metres out, looking at the pad. */
  w.rig.suspended = true;
  const cam = w.camera;
  cam.position.set(st.x + 78, c + 16, st.z + 78);
  cam.lookAt(st.x, c + 8, st.z);
  cam.updateMatrixWorld();
  return {uid: st.uid, x: st.x, z: st.z, centre: +c.toFixed(2),
          core: t.core.size, coreSeg: t.core.N,
          outsideCore: Math.abs(st.x - t.cx) > t.core.size / 2 ||
                       Math.abs(st.z - t.cz) > t.core.size / 2,
          padMax: +Math.max(...dev).toFixed(2), padMin: +Math.min(...dev).toFixed(2)};
}, STATION);
console.log(JSON.stringify(info));
await page.waitForTimeout(700);
const out = args.out || `/Users/rynatical/LAB-lem/scratchpad/shots/padshot-L${LAYOUT}-${STATION}.png`;
fs.writeFileSync(out, await page.screenshot());
if (errs.length) console.log('errors:', errs.slice(0, 4));
await browser.close();
