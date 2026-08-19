/* pl-berth.mjs — does a parked train stand clear of the crossover's block?
 *
 * The one thing a mid-rank turnout can break that no geometry check would see:
 * `trains.js:_onRoad` returns false for a consist whose body overlaps a JUNCTION
 * span, and a working that never reads as home never starts again. The crossover
 * puts a junction span between two stands, so the margins are small and derived
 * (`LINK_BLOCK_GAP`) rather than generous. This measures them on parked trains.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,buildings,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
/* fill the rank */
for (const uid of ['multitek-ns', 'multitek-s', 'optimpp-1', 'koehler-cp',
                   'optimpp-2', 'pac-flash-1', 'pac-flash-2']) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'PL-BERTH'), uid);
  await p.waitForTimeout(200);
}
await p.waitForTimeout(75000);      // longer than a lap, so they come home

console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = window.__lemWorld.subsystems.get('trains');
  const road = rail.tracks.find(t => t.name === 'load:0');
  const junc = (rail._sections.get('load:0') || [])
    .map((s, i) => ({i, a: +s.a.toFixed(1), b: +s.b.toFixed(1), junction: s.junction}))
    .filter(s => s.junction);
  const stands = [];
  for (const [uid, sd] of rail.sidings) {
    if (sd.track !== road) continue;
    stands.push({uid, s: +sd.sDock.toFixed(1)});
  }
  stands.sort((a, c) => a.s - c.s);
  /* how close each parked rake's head and tail get to a junction span */
  const rows = stands.map(st => {
    const c = T.consists.find(x => x.uid === st.uid);
    const head = st.s, tail = st.s - (c ? c.length : 84);
    let worst = Infinity, which = null;
    for (const j of junc) {
      const over = Math.min(head, j.b) - Math.max(tail, j.a);
      if (over > 0) { worst = -over; which = `INSIDE ${j.a}..${j.b}`; break; }
      const d = Math.min(Math.abs(j.a - head), Math.abs(tail - j.b));
      if (d < worst) { worst = d; which = `${j.a}..${j.b}`; }
    }
    return {uid: st.uid, standS: st.s, rake: c ? +c.length.toFixed(1) : null,
            tailS: +tail.toFixed(1), state: c ? c.state : null,
            onRoad: c ? T._onRoad(c) : null,
            nearestJunctionSpan: which, clearanceM: +worst.toFixed(2)};
  });
  return {junctionSpans: junc, stands: rows,
          roadBlocks: road.blocks.map(x => x.map(v => +v.toFixed(1)))
                          .sort((a, c) => a[0] - c[0])};
}), null, 1));
await b.close();
