/* tally.mjs — every interval overlap the soak would report, split by whether
 * the two consists are even measured in the same coordinate, and checked
 * against world-space separation.
 *
 * Not a replacement for soak.mjs and not a looser version of it: it reports the
 * SAME condition and adds the two facts needed to tell a real collision from a
 * comparison of two different measurements. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(2000);

await p.evaluate(() => {
  const W = window.__lemWorld;
  window.__tally = {sameCoord: 0, crossCoord: 0, worstSameCoord: null,
                    worstWorld: Infinity, samples: []};
  const seen = new Set();
  const tick = () => {
    const T = W.subsystems.get('trains');
    const live = (T?.consists || []).filter(c => c && c.group && c.group.visible && c.route);
    for (let i = 0; i < live.length; i++)
      for (let j = i + 1; j < live.length; j++) {
        const a = live[i], bb = live[j];
        if (!a.line || a.line !== bb.line) continue;
        const aS = a.s - a.length, aE = a.s, bS = bb.s - bb.length, bE = bb.s;
        if (!(aS < bE - 0.5 && bS < aE - 0.5)) continue;
        const same = a.route === bb.route;
        const t = window.__tally;
        if (same) t.sameCoord++; else t.crossCoord++;
        const pt = (c, s) => { const L = c.route.len || 1;
          const u = c.route.closed ? (((s % L) + L) % L) / L
                                   : Math.min(1, Math.max(0, s / L));
          return c.route.getPointAt ? c.route.getPointAt(u) : null; };
        let wm = Infinity;
        for (let u = 0; u <= 8; u++) {
          const pA = pt(a, a.s - a.length * (u / 8)); if (!pA) break;
          for (let v = 0; v <= 8; v++) {
            const pB = pt(bb, bb.s - bb.length * (v / 8)); if (!pB) break;
            const d = Math.hypot(pA.x - pB.x, pA.y - pB.y, pA.z - pB.z);
            if (d < wm) wm = d;
          }
        }
        if (wm < t.worstWorld) t.worstWorld = +wm.toFixed(2);
        const key = same + '|' + a.slot + '/' + bb.slot;
        if (!seen.has(key) && t.samples.length < 40) {
          seen.add(key);
          t.samples.push({same, world: +wm.toFixed(2), line: a.line,
                          pair: a.slot + '/' + bb.slot,
                          a: [+aS.toFixed(1), +aE.toFixed(1), a.state],
                          b: [+bS.toFixed(1), +bE.toFixed(1), bb.state]});
        }
        if (same && (!t.worstSameCoord || wm < t.worstSameCoord.world)) {
          t.worstSameCoord = {world: +wm.toFixed(2), pair: a.slot + '/' + bb.slot,
                              line: a.line, aState: a.state, bState: bb.state,
                              aHolds: a.holds ? [...a.holds] : null,
                              bHolds: bb.holds ? [...bb.holds] : null};
        }
      }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
const BAY = 2.05;
let seed = 12345;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const SEQ = [[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]]];
for (let L = 1; L < 10; L++) {
  const kind = L % 4, pos = [];
  for (let i = 0; i < 7; i++) {
    if (kind === 0) pos.push([Math.round(rnd() * 8) * BAY, Math.round(rnd() * 8) * BAY]);
    else if (kind === 1) pos.push([i * BAY, 0]);
    else if (kind === 2) pos.push([0, i * BAY]);
    else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]);
  }
  if (kind === 3) pos[1] = pos[0].slice();
  SEQ.push(pos);
}
for (let L = 0; L < SEQ.length; L++) {
  await p.evaluate(pp => {
    const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
    window.__lemWorld.setMachines(F.map((uid, i) => ({
      machine_uid: uid, title: uid, status: 'GREEN', pos: pp[i], reason: 'soak',
      sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    })));
  }, SEQ[L]);
  await p.waitForTimeout(2500);
  for (let i = 0; i < 50; i++) {
    await p.evaluate(u => window.__lemWorld.parse(u, 'L-SOAK'), FLEET[i % 7]);
    await p.waitForTimeout(120);
  }
  await p.waitForTimeout(4000);
  const t = await p.evaluate(() => ({s: window.__tally.sameCoord,
                                     x: window.__tally.crossCoord}));
  process.stdout.write(`layout ${L}: sameCoord=${t.s} crossCoord=${t.x}\n`);
}
const t = await p.evaluate(() => window.__tally);
console.log(JSON.stringify(t, null, 1));
console.log('errors:', errs.slice(0, 6));
fs.writeFileSync('/tmp/tally.json', JSON.stringify(t, null, 2));
await b.close();
