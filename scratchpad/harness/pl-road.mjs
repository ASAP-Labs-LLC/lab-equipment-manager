/* pl-road.mjs — what the loading roads actually are, at the granted pitch.
 * Reports each load:<z> road, its stands, gaps, apron, and the branch beside it,
 * plus the arithmetic the mid-rank connection has to fit inside. */
import {chromium} from 'playwright';
const LAYOUT = process.argv.includes('--layout')
  ? process.argv[process.argv.indexOf('--layout') + 1] : null;
const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
const q = LAYOUT ? `&layout=${LAYOUT}` : '';
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra${q}`, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const out = {roads: [], meta: {}};
  const seen = new Set();
  for (const [uid, sd] of rail.sidings) {
    if (seen.has(sd.track.name)) continue;
    seen.add(sd.track.name);
    const t = sd.track, line = sd.line;
    const ds = (sd.row?.list || []).map(st => ({
      uid: st.uid, x: +st.x.toFixed(1),
      s: +t.nearest(st.x, sd.dockZ).s.toFixed(1),
    })).sort((a, b) => a.s - b.s);
    const gaps = ds.slice(1).map((d, i) => +(d.s - ds[i].s).toFixed(1));
    const br = rail.branchOf.get(line);
    let cyc = null;
    try { cyc = rail.cycle(uid); } catch {}
    out.roads.push({
      road: t.name, line: line.name,
      len: +t.length.toFixed(1),
      renderFrom: +(t.renderFrom || 0).toFixed(1),
      renderTo: +Math.min(t.renderTo, t.length).toFixed(1),
      paved: t.paved ? t.paved.map(v => +v.toFixed(1)) : null,
      blocks: t.blocks.map(x => x.map(v => +v.toFixed(1))),
      stands: ds, gaps,
      sIn: +sd.sIn.toFixed(1), sOut: +sd.sOut.toFixed(1),
      dockZ: +sd.dockZ.toFixed(1),
      lineLen: +line.length.toFixed(1),
      lineFrom: +(line.renderFrom || 0).toFixed(1),
      lineTo: +Math.min(line.renderTo, line.length).toFixed(1),
      br: br ? {jS: +br.jS.toFixed(1), eS: +br.eS.toFixed(1),
                tS: +br.tS.toFixed(1), teS: +(br.teS ?? NaN).toFixed(1)} : null,
      cyc: cyc ? {len: +cyc.route.length.toFixed(1), closed: cyc.closed,
                  segs: cyc.segments.length, nVariants: cyc.variants?.length ?? 0,
                  docks: cyc.docks.map(d => +d.s.toFixed(1))} : null,
    });
  }
  out.meta = {
    branches: rail.branches.length,
    tracks: rail.tracks.map(t => t.name),
    oneWay: rail.oneWayReport(),
    joints: (() => { const j = rail.jointReport();
      return {joins: j.joins, worstGapMm: +j.worstGapMm.toFixed(2),
              worstAngle: +j.worstAngle.toFixed(3),
              worstLevelMm: +j.worstLevelMm.toFixed(2)}; })(),
  };
  return out;
}), null, 1));
await b.close();
