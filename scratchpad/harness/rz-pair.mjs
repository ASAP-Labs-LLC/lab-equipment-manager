/* rz-pair.mjs — the static form of soak's collision counter.
 *
 * soak.mjs runs trains and reports two slots that came within 5 m. That is a
 * SEARCH: it finds a fouling pair only if traffic happens to put two workings
 * there. The block table can be interrogated directly instead. For every pair
 * of blocks on DIFFERENT tracks, the minimum distance between the two pieces of
 * metal is a fixed property of the built railway; if that distance is under the
 * fouling threshold and nothing couples the two blocks, then two trains holding
 * one each are legally separated and physically touching, and the only question
 * is whether traffic ever puts them there.
 *
 * Same-track pairs are excluded: adjacent blocks on one road always meet at
 * their joint, and along-track separation is trains.js's lookahead, not the
 * table's job.
 *
 *   node rz-pair.mjs [--layout 0] [--foul 5] [--all]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const FOUL = parseFloat(arg('foul', '5'));
const ALL = process.argv.includes('--all');
const NOCOUPLE = process.argv.includes('--nocouple');

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
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
const POS = layouts(LAYOUT + 1)[LAYOUT];

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
  fleet.map(([uid, title, status], i) => ({
    machine_uid: uid, title, status, pos: pos[i], reason: 'rzpair',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

console.log(JSON.stringify(await p.evaluate(([FOUL, ALL, NOCOUPLE]) => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = rail.tracks.filter(t => t && t.frames && t.length > 4);
  const extent = t => Math.min(t.length, (t.frames.count - 1.001) * t.frames.step);
  /* every block, as a bag of points, 1 m apart */
  const blocks = [];
  const sections = {};
  for (const t of T) {
    const list = rail._sections?.get(t.name) || [];
    const e = extent(t);
    sections[t.name] = list.map((s, i) => `${t.name}#${i}${s.junction ? '*' : ''} ` +
                                          `${s.a.toFixed(1)}..${s.b.toFixed(1)}`);
    for (let i = 0; i < list.length; i++) {
      const s = list[i];
      const a = Math.min(s.a, e), bb = Math.min(s.b, e);
      if (!(bb > a)) continue;
      const pts = [];
      for (let u = a; u <= bb; u += 1) pts.push(t.at(u).position);
      pts.push(t.at(bb).position);
      /* The EFFECTIVE id. A section that carries its own `id` has been coupled
       * to another road's block by `_coupleThroats`, and two sections with one
       * id are one block however many tracks they lie on. */
      const id = (NOCOUPLE ? null : s.id) || `${t.name}#${i}`;
      const prev = blocks.find(x => x.id === id);
      if (prev) { prev.pts.push(...pts); prev.parts.push(`${t.name}#${i}`); continue; }
      blocks.push({id, track: t.name, a: s.a, b: s.b, parts: [`${t.name}#${i}`],
                   junction: !!s.junction, pts});
    }
  }
  /* which extra ids does standing anywhere in this block also claim? */
  const claims = bk => {
    const out = new Set([bk.id]);
    const run = rail.runFor?.(bk.id);
    if (run) for (const r of run) out.add(r);
    return out;
  };
  const rows = [];
  let worstOk = Infinity;
  for (let i = 0; i < blocks.length; i++) {
    for (let j = i + 1; j < blocks.length; j++) {
      const A = blocks[i], B = blocks[j];
      if (A.id === B.id) continue;
      /* Same-track pairs are excluded: two blocks on one road meet at their
       * joint and along-track separation is trains.js's lookahead. A COUPLED
       * block spans two roads, so it is compared against everything it does
       * not itself contain. */
      if (A.parts.every(p => B.parts.some(q => q.split('#')[0] === p.split('#')[0])) ||
          B.parts.every(p => A.parts.some(q => q.split('#')[0] === p.split('#')[0])))
        continue;
      let d = Infinity;
      for (const pa of A.pts) for (const pb of B.pts) {
        const dd = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
        if (dd < d) d = dd;
      }
      if (d >= FOUL) continue;
      const ca = claims(A), cb = claims(B);
      let coupled = false;
      for (const x of ca) if (cb.has(x)) { coupled = true; break; }
      if (coupled && !ALL) continue;
      if (!coupled && d < worstOk) worstOk = d;
      rows.push({a: A.parts.join('=') + (A.junction ? '*' : ''),
                 b: B.parts.join('=') + (B.junction ? '*' : ''),
                 gap: +d.toFixed(2), coupled});
    }
  }
  rows.sort((x, y) => x.gap - y.gap);
  return {
    sections,
    coupled: blocks.filter(b => b.parts.length > 1).map(b => b.id + ' = ' + b.parts.join(' + ')),
    foulingPairsUncoupled: rows.filter(r => !r.coupled).length,
    worstUncoupledGap: Number.isFinite(worstOk) ? +worstOk.toFixed(2) : null,
    rows: rows.slice(0, 60),
  };
}, [FOUL, ALL, NOCOUPLE]), null, 1));
await b.close();
