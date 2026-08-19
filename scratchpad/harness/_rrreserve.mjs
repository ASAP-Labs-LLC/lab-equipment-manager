/* _rrreserve.mjs — how many metres of declared deck are approach reserve?
 * A reserve station is one inside a viaduct/bridge span whose own depth is
 * BELOW the threshold that would have made it a structure. Counted inward
 * from each end until the first genuine structure station. */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i+1];
const layouts = parseInt(a.layouts || '3', 10);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0,300)));
let grand = 0, deckTotal = 0;
for (let L = 0; L < layouts; L++) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`,
               {waitUntil: 'load', timeout: 120000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
  await p.waitForTimeout(2500);
  const r = await p.evaluate(() => {
    const W = window.__lemWorld, rail = W.subsystems.get('rail');
    const VIADUCT_FILL = 6.0, WET_FREEBOARD = 2.5;
    const rows = []; let res = 0, deck = 0;
    for (const t of rail.tracks) {
      const f = t.frames, G = t.groundY;
      if (!f || !G || G.length !== f.count) continue;
      const FORMATION = f.pos[0*3+1] - (f.pos[0*3+1]); // unused
      let ws = []; try { ws = t.earthworks(); } catch { continue; }
      const wet = Number.isFinite(t.waterY) ? t.waterY + WET_FREEBOARD : -Infinity;
      for (const s of ws) {
        if (s.kind !== 'viaduct' && s.kind !== 'bridge') continue;
        // depth at station i, reconstructed the same way earthworks() does
        const dep = i => (f.pos[i*3+1] - 0.69) - G[i];
        const isStruct = i => s.kind === 'bridge' ? G[i] <= wet : dep(i) > VIADUCT_FILL;
        let a0 = s.i0, a1 = s.i1;
        while (a0 <= a1 && !isStruct(a0)) a0++;
        while (a1 >= a0 && !isStruct(a1)) a1--;
        const backM = (a0 - s.i0) * f.step, fwdM = (s.i1 - a1) * f.step;
        res += backM + fwdM; deck += s.length;
        rows.push({t: t.name, kind: s.kind, from: +s.from.toFixed(1), to: +s.to.toFixed(1),
                   len: +s.length.toFixed(1), back: +backM.toFixed(1), fwd: +fwdM.toFixed(1),
                   core: +((a1 - a0) * f.step).toFixed(1)});
      }
    }
    return {rows, res: +res.toFixed(1), deck: +deck.toFixed(1)};
  });
  console.log(`layout ${L}: deck ${r.deck} m, reserve ${r.res} m`);
  for (const q of r.rows)
    console.log(`   ${q.t} ${q.kind} ${q.from}-${q.to} len ${q.len}  back ${q.back} + core ${q.core} + fwd ${q.fwd}`);
  grand += r.res; deckTotal += r.deck;
}
console.log(`\nTOTAL over ${layouts} layouts: deck ${deckTotal.toFixed(1)} m, reserve ${grand.toFixed(1)} m`);
await b.close();
