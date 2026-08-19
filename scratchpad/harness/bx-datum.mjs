/* bx-datum.mjs — SITE_Y against the schedule's own `datumAbsolute`, ablated in
 * one page load. Three surfaces, same probe cells, same frame. */
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
  const pts = [];
  for (const bb of sb.benches) for (const q of mod.benchProbePoints(bb.probe)) pts.push(q);
  const blk = [];
  for (let x = t.cx - 260; x <= t.cx + 260; x += 10)
    for (let z = t.cz - 200; z <= t.cz + 200; z += 10) blk.push([x, z]);
  const stat = (P) => {
    let n = 0, s = 0, cut = 0, fill = 0, mxC = 0, mxF = 0;
    for (const [x, z] of P) {
      const m = t._designAt(x, z) - t._smoothBase(x, z);
      n++; s += Math.abs(m);
      if (m < 0) { cut++; if (-m > mxC) mxC = -m; } else { fill++; if (m > mxF) mxF = m; }
    }
    return {meanAbsMoveM: r2(s / n), fillToCut: r2(fill / Math.max(1, cut)),
            maxCutM: r2(mxC), maxFillM: r2(mxF)};
  };
  const benchMoves = () => sb.benches.map(bb => ({id: bb.id,
    builtY: r2(t._designAt(bb.cx, bb.cz)),
    moveM: r2(t._designAt(bb.cx, bb.cz) - bb.naturalM)}));
  const T = t._terrace, keep = T.datumY;
  const out = {};
  out.siteY = {datumY: r2(keep), probe: stat(pts), block: stat(blk), benches: benchMoves()};
  T.datumY = sb.datumAbsolute;
  out.scheduleDatum = {datumY: r2(T.datumY), probe: stat(pts), block: stat(blk),
                       benches: benchMoves()};
  T.datumY = keep;
  const terrace = t._terrace; t._terrace = null;
  out.plane = {probe: stat(pts), block: stat(blk),
               benches: sb.benches.map(bb => ({id: bb.id,
                 builtY: r2(t._designAt(bb.cx, bb.cz)),
                 moveM: r2(t._designAt(bb.cx, bb.cz) - bb.naturalM)}))};
  t._terrace = terrace;
  out.datumAbsolute = r2(sb.datumAbsolute);
  return out;
}), null, 1));
await b.close();
