/* audit-rail.mjs — independent audit of the track PLAN.
 * Does not trust rail.oneWayReport(); recomputes everything from segments. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[a.slice(2)] = true; else { args[a.slice(2)] = n; i++; }
}
const url = args.url;
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 900, height: 600}});
const errors = [];
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0,300)); });
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0,300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(1500);

const dump = await page.evaluate(() => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const out = {stations: w.plan.stations.length, hub: w.plan.hub ? {x: w.plan.hub.x, z: w.plan.hub.z} : null};

  out.tracks = (rail.tracks || []).map(t => {
    const f = t.frames;
    const pt = (i) => f ? [Math.round(f.pos[i*3]), Math.round(f.pos[i*3+2])] : null;
    // sample the polyline every ~40 points for a plan drawing
    const poly = [];
    if (f) { const step = Math.max(1, Math.floor(f.count/220));
      for (let i = 0; i < f.count; i += step) poly.push([+f.pos[i*3].toFixed(1), +f.pos[i*3+2].toFixed(1)]);
      poly.push([+f.pos[(f.count-1)*3].toFixed(1), +f.pos[(f.count-1)*3+2].toFixed(1)]); }
    return {name: t.name, len: +t.length.toFixed(1), a: pt(0), b: f ? pt(f.count-1) : null, poly};
  });

  out.branches = (rail.branches || []).map(b => ({
    track: b.track?.name, jS: b.jS, tS: b.tS, teS: b.teS, eS: b.eS,
    zW: b.zW, zE: b.zE, row: b.row?.list?.length}));

  out.sidings = [];
  for (const [uid, sd] of rail.sidings) out.sidings.push({uid, track: sd.track.name,
    line: sd.line?.name, entryS: sd.entryS, exitS: sd.exitS, sIn: sd.sIn, sOut: sd.sOut,
    dockZ: sd.dockZ});

  out.circuits = [];
  const seenTrack = new Set();
  for (const s of w.plan.stations) {
    let c = null;
    try { c = rail.cycle(s.uid); } catch (e) { c = {err: String(e)}; }
    if (!c) { out.circuits.push({uid: s.uid, cycle: null}); continue; }
    const key = c.line + '|' + (c.segments||[]).map(g=>g.track+g.s0.toFixed(1)).join(',');
    const rec = {uid: s.uid, line: c.line, closed: c.closed, turned: c.turned,
      len: +c.route.length.toFixed(1), terminal: +(c.terminal ?? -1).toFixed(1),
      loopExit: +(c.loopExit ?? -1).toFixed(1), dockS: +(c.dockS ?? -1).toFixed(1),
      docks: (c.docks||[]).map(d => +d.s.toFixed(1)),
      segments: (c.segments||[]).map(g => ({track: g.track, from: g.from, to: g.to,
        s0: +g.s0.toFixed(1), s1: +g.s1.toFixed(1),
        aS: +c.route.acc[g.from].toFixed(1), bS: +c.route.acc[g.to].toFixed(1)})),
      gap: +c.route.points[0].distanceTo(c.route.points[c.route.points.length-1]).toFixed(4),
      dup: seenTrack.has(key)};
    seenTrack.add(key);
    out.circuits.push(rec);
  }

  out.turnouts = (rail._turnouts||[]).map(r => ({on: r.track?.name, child: r.child?.name,
    pdir: r.pdir, s: r.s !== undefined ? +(+r.s).toFixed(1) : undefined,
    x: r.x !== undefined ? Math.round(r.x) : undefined,
    z: r.z !== undefined ? Math.round(r.z) : undefined,
    keys: Object.keys(r)}));

  try { out.oneWayReport = rail.oneWayReport(); if (out.oneWayReport.circuits === undefined) {} } catch (e) { out.oneWayReport = {err: String(e)}; }
  if (out.oneWayReport && out.oneWayReport.circuits !== undefined) {
    // strip non-serialisable
    out.oneWayReport = JSON.parse(JSON.stringify(out.oneWayReport));
  }

  try { out.blockSpans = rail.blockSpans(rail.cycle(w.plan.stations[0].uid)).map(b => ({id: b.id, a: +b.a.toFixed(1), b: +b.b.toFixed(1), junction: b.junction, run: b.run})); } catch(e){ out.blockSpans = String(e); }

  out.signals = (rail.signals||[]).map(s => ({key: s.key, s: s.s !== undefined ? +(+s.s).toFixed(1) : undefined, shown: s.shown}));
  const tr = w.subsystems.get('trains');
  out.consists = tr ? tr.consists.filter(c=>!c.shunt).map(c => ({slot: c.slot, uid: (c.uid||'').slice(0,10), state: c.state, s: Math.round(c.s||0), len: c.route?Math.round(c.route.len):null})) : [];
  return out;
});
await browser.close();
fs.writeFileSync(args.out || '/tmp/rail-audit.json', JSON.stringify({dump, errors}, null, 1));
console.log('stations', dump.stations, 'tracks', dump.tracks.length, 'circuits', dump.circuits.length, 'errors', errors.length);
console.log(JSON.stringify(dump.oneWayReport, null, 1));
