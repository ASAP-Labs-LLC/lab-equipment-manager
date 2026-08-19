/* tv-sweep.mjs — pl-pass.mjs's passing move, measured against EVERY stand the
 * leaving working runs past, not just the next one.
 *
 * pl-pass reports the minimum body-to-body clearance between a working leaving
 * by a mid-rank crossover and a rake standing at the NEXT stand. That is the
 * tightest point of the divergence, but it is not the whole move: once the
 * working is on the branch it runs the length of the rest of the rank, past
 * every other stand on it, and every one of those may have an 84 m rake on it.
 * If the branch runs closer to the road than 5 m anywhere along there, consuming
 * the variants trades a stall for a collision and the soak will find it as
 * `collision` — so it is measured here first, deliberately, before the file is
 * changed.
 *
 *   node tv-sweep.mjs [--rake 84] [--layout 0|1]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const RAKE = parseFloat(arg('rake', '84'));
const LAYOUT = parseInt(arg('layout', '0'), 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const ONE_RANK = FLEET.map((_, i) => [i * 2.05, 0]);

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvsweep',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(2800);
}

const out = await p.evaluate(RAKE => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const res = [];
  const seen = new Set();
  const P = (route, s) => route.pointAtDistance(
    Math.max(0, Math.min(route.length, s)));
  for (const [uid, sd] of rail.sidings) {
    if (seen.has(sd.track.name)) continue;
    let cyc = null; try { cyc = rail.cycle(uid); } catch { continue; }
    if (!cyc?.variants || cyc.variants.length < 2) continue;
    seen.add(sd.track.name);
    const cycles = new Map();
    for (const st of sd.row.list) {
      try { cycles.set(st.uid, rail.cycle(st.uid)); } catch {}
    }
    for (let vi = 0; vi < cyc.variants.length - 1; vi++) {
      const relDock = cyc.variants[vi].docks[cyc.variants[vi].docks.length - 1];
      const relCyc = cycles.get(relDock.uid);
      const v = relCyc?.variants?.find(x => x.line === cyc.variants[vi].line);
      if (!v) continue;
      const fullRel = relCyc.variants[relCyc.variants.length - 1];
      const idx = fullRel.docks.findIndex(d => d.uid === relDock.uid);
      if (idx < 0) continue;
      /* every stand it runs past, not just the next one */
      const rows = [];
      for (let k = idx + 1; k < fullRel.docks.length; k++) {
        const aheadS = fullRel.docks[k].s;
        const run = route => {
          let worst = Infinity, at = 0;
          for (let h = v.dockS; h < Math.min(route.length, v.dockS + 900); h += 1) {
            let d = Infinity;
            for (let o = 0; o <= RAKE; o += 3) {
              const q = P(route, h - o);
              for (let e = 0; e <= RAKE; e += 3) {
                const r = P(fullRel.route, aheadS - e);
                const dd = Math.hypot(q.x - r.x, q.y - r.y, q.z - r.z);
                if (dd < d) d = dd;
              }
            }
            if (d < worst) { worst = d; at = h; }
          }
          return {min: +worst.toFixed(2), at: +at.toFixed(1)};
        };
        rows.push({stand: fullRel.docks[k].uid, standS: +aheadS.toFixed(1),
                   loop: run(v.route)});
      }
      res.push({road: sd.track.name, variant: v.line, releases: relDock.uid,
                dockS: +v.dockS.toFixed(1), rows});
    }
  }
  return res;
}, RAKE);

for (const r of out) {
  console.log(`${r.road}  ${r.variant}  releases ${r.releases} (stand ${r.dockS} m)`);
  for (const row of r.rows) {
    const flag = row.loop.min < 5 ? '  *** FOULS ***' : '';
    console.log(`   vs 84 m rake at ${row.stand} (${row.standS} m): ` +
                `${row.loop.min} m  (at s=${row.loop.at})${flag}`);
  }
  console.log('');
}
if (!out.length) console.log('no road on this layout publishes a variant');
await b.close();
