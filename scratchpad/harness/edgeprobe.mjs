/* edgeprobe.mjs — where the ground steps, and by how much.
 *
 *   node edgeprobe.mjs [--layouts 10] [--stride 20] [--radius 4000]
 *
 * The soak reports the FIRST step past its threshold on each of eight bearings
 * and stops, which tells you a fault exists but not what shape it is. This walks
 * the same layouts on a dense polar grid, reports the worst step per layout with
 * the radius it happened at, and — because the two suspects look identical in a
 * single number — separates "the graded core and the analytic ring disagree at
 * the seam" from "the analytic hills are simply steeper than any hillside".
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const LAYOUTS = parseInt(args.layouts || '10', 10);
const STRIDE = parseFloat(args.stride || '20');
const RADIUS = parseFloat(args.radius || '4000');

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

const PROBE = ([stride, radius]) => {
  const w = window.__lemWorld;
  const t = w.subsystems.get('terrain');
  const plan = w.plan;
  if (!t || !t.heightAt || !plan) return {error: 'no terrain'};
  const cx = (plan.bounds.minX + plan.bounds.maxX) / 2;
  const cz = (plan.bounds.minZ + plan.bounds.maxZ) / 2;
  const worst = [];
  let bad = 0, samples = 0, nonfinite = 0;
  for (let b = 0; b < 32; b++) {
    const a = (b / 32) * Math.PI * 2;
    let prev = t.heightAt(cx, cz), pr = 0;
    for (let r = stride; r < radius; r += stride) {
      const x = cx + Math.cos(a) * r, z = cz + Math.sin(a) * r;
      const h = t.heightAt(x, z);
      samples++;
      if (!isFinite(h)) { nonfinite++; break; }
      const d = Math.abs(h - prev);
      if (d > 26) bad++;
      worst.push({d, r, b, from: prev, to: h, x, z});
      prev = h; pr = r;
    }
  }
  worst.sort((p, q) => q.d - p.d);
  /* The seam the core cuts against its first ring, so a step can be attributed
   * rather than guessed at. */
  const core = t.core ? {size: t.core.size, x0: t.core.x0, z0: t.core.z0,
                         cx: t.cx, cz: t.cz, step: t.core.step, N: t.core.N} : null;
  /* Straight comparison of the graded field against whatever the world answers
   * just outside it, along +x from the site centre. */
  const seam = [];
  if (core) {
    const half = core.size / 2;
    for (const s of [-2, -1, -0.25, 0.25, 1, 2]) {
      const x = t.cx + half + s;
      seam.push({off: s, h: t.heightAt(x, t.cz)});
    }
  }
  return {cx, cz, samples, bad, nonfinite, core, seam,
          worst: worst.slice(0, 6).map(o => ({d: +o.d.toFixed(1), r: o.r, b: o.b,
            from: +o.from.toFixed(1), to: +o.to.toFixed(1),
            x: Math.round(o.x), z: Math.round(o.z)})),
          bounds: plan.bounds};
};

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=terrain&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 800, height: 480}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});

let totalBad = 0, worstAll = 0;
for (const [L, positions] of layouts(LAYOUTS).entries()) {
  await page.evaluate(([fleet, pos]) => {
    window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'probe',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    })));
  }, [FLEET, positions]);
  await page.waitForTimeout(1200);
  const r = await page.evaluate(PROBE, [STRIDE, RADIUS]);
  totalBad += r.bad || 0;
  const w0 = r.worst && r.worst[0] ? r.worst[0].d : 0;
  if (w0 > worstAll) worstAll = w0;
  const span = r.bounds ? `${Math.round(r.bounds.maxX - r.bounds.minX)}x${Math.round(r.bounds.maxZ - r.bounds.minZ)}m` : '?';
  console.log(`L${L} site ${span} core ${r.core ? r.core.size : '-'}m ` +
              `bad=${r.bad}/${r.samples} nonfinite=${r.nonfinite} worst=${w0}m`);
  for (const o of (r.worst || []).slice(0, 3)) {
    console.log(`    ${o.d}m at r=${o.r} b=${o.b} (${o.from} -> ${o.to}) @ ${o.x},${o.z}`);
  }
  if (r.seam) console.log('    seam:', r.seam.map(s => `${s.off}:${s.h.toFixed(1)}`).join(' '));
}
console.log(`\nTOTAL bad steps: ${totalBad}   worst: ${worstAll}m`);
if (errs.length) console.log('console errors:', errs.slice(0, 5));
await browser.close();
process.exit(totalBad ? 1 : 0);
