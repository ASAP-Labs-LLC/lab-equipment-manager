/* bx-earth.mjs — how much earth each design surface moves, in ONE frame.
 *
 * ix-verify.mjs reports `planeMeanAbsMoveM` from `t._designAt(x,z) - t.yShift`
 * against `t._smoothBase(x,z)`, and `_smoothBase` already CARRIES `yShift`
 * (`_baseHeight` adds it on its last line). So that comparison is out by yShift,
 * which on the real floor is -12.57 m. Both surfaces are read in the shifted
 * frame here, and the bench figure is read off the terrace this file actually
 * built rather than off `levelAbsolute`, which is quoted in the sampler frame.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const mod = await import('/static/world/index.js');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const sb = w.ctx.siteBenches;
  const terrace = t._terrace;
  const stat = (pts, designFn) => {
    let n = 0, s = 0, cut = 0, fill = 0, mxC = 0, mxF = 0;
    for (const [x, z] of pts) {
      const nat = t._smoothBase(x, z), d = designFn(x, z);
      const m = d - nat;
      n++; s += Math.abs(m);
      if (m < 0) { cut++; if (-m > mxC) mxC = -m; } else { fill++; if (m > mxF) mxF = m; }
    }
    return {cells: n, meanAbsMoveM: r2(s / n), cut, fill,
            fillToCut: r2(fill / Math.max(1, cut)),
            maxCutM: r2(mxC), maxFillM: r2(mxF)};
  };
  const pts = [];
  for (const bb of sb.benches) for (const q of mod.benchProbePoints(bb.probe)) pts.push(q);
  /* the wider station block, the window ix-bench.mjs used */
  const blk = [];
  for (let x = t.cx - 260; x <= t.cx + 260; x += 10)
    for (let z = t.cz - 200; z <= t.cz + 200; z += 10) blk.push([x, z]);

  const benchedProbe = stat(pts, (x, z) => t._designAt(x, z));
  const benchedBlock = stat(blk, (x, z) => t._designAt(x, z));
  t._terrace = null;
  const planeProbe = stat(pts, (x, z) => t._designAt(x, z));
  const planeBlock = stat(blk, (x, z) => t._designAt(x, z));
  t._terrace = terrace;

  /* and the fall each surface expresses over the seven stations + hub */
  const cs = [...w.plan.stations.map(s => [s.x, s.z]), [w.plan.hub.x, w.plan.hub.z]];
  const span = fn => {
    const v = cs.map(([x, z]) => fn(x, z));
    return r2(Math.max(...v) - Math.min(...v));
  };
  const benchedSpan = span((x, z) => t._designAt(x, z));
  t._terrace = null;
  const planeSpan = span((x, z) => t._designAt(x, z));
  t._terrace = terrace;
  return {probeCells: {benched: benchedProbe, plane: planeProbe},
          stationBlock: {benched: benchedBlock, plane: planeBlock},
          fallOverStationsM: {benched: benchedSpan, plane: planeSpan},
          yShift: r2(t.yShift)};
}), null, 1));
await b.close();
