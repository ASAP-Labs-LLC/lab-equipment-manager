/* _rrrank.mjs — is there room in a rank for a mid-rank connection?
 * Per loading road: the stands, their arc length on the road, the gaps between
 * them, the road's laid window, the existing leads' length, and the apron. */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i+1];
const layouts = parseInt(a.layouts || '6', 10);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0,300)));
for (let L = 0; L < layouts; L++) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`,
               {waitUntil: 'load', timeout: 120000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
  await p.waitForTimeout(2000);
  const r = await p.evaluate(() => {
    const W = window.__lemWorld, rail = W.subsystems.get('rail');
    const roads = new Map();
    for (const [uid, sd] of rail.sidings) {
      const key = sd.track.name;
      if (!roads.has(key)) roads.set(key, {name: key, line: sd.line.name,
        len: +sd.track.length.toFixed(1),
        from: +(sd.track.renderFrom||0).toFixed(1),
        to: +Math.min(sd.track.renderTo, sd.track.length).toFixed(1),
        paved: sd.track.paved ? sd.track.paved.map(v=>+v.toFixed(1)) : null,
        stands: []});
      roads.get(key).stands.push({uid, s: +sd.sDock.toFixed(1)});
    }
    const out = [];
    for (const r of roads.values()) {
      r.stands.sort((a,b)=>a.s-b.s);
      const gaps = [];
      for (let i=1;i<r.stands.length;i++) gaps.push(+(r.stands[i].s-r.stands[i-1].s).toFixed(1));
      // free straight on the PARENT branch alongside each gap
      out.push({...r, n: r.stands.length, gaps,
        headM: +(r.stands[0].s - r.from).toFixed(1),
        tailM: +(r.to - r.stands[r.stands.length-1].s).toFixed(1)});
    }
    // branch geometry: how much of each branch is straight past the docks
    const br = [];
    for (const t of rail.tracks) {
      if (!/^branch/.test(t.name)) continue;
      br.push({name: t.name, len: +t.length.toFixed(1),
               from: +(t.renderFrom||0).toFixed(1),
               to: +Math.min(t.renderTo,t.length).toFixed(1),
               minR: t.minRadiusUsed ? +t.minRadiusUsed.toFixed(0) : null});
    }
    return {roads: out, branches: br,
            tracks: rail.tracks.map(t=>t.name)};
  });
  console.log(`--- layout ${L} --- tracks: ${r.tracks.join(', ')}`);
  for (const q of r.roads) {
    console.log(`  ${q.name} off ${q.line}: length ${q.len}, laid ${q.from}..${q.to}, paved ${q.paved}`);
    console.log(`     ${q.n} stands at [${q.stands.map(s=>s.s).join(', ')}]  gaps [${q.gaps.join(', ')}]  head ${q.headM} tail ${q.tailM}`);
  }
  for (const q of r.branches) console.log(`  ${q.name}: len ${q.len} laid ${q.from}..${q.to} minR ${q.minR}`);
}
await b.close();
