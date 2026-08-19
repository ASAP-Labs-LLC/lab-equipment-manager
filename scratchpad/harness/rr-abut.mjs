/* rr-abut.mjs — is there a visible abutment, or does the deck just stop?
 *
 * A bridge reads as spanning something because you can see the thing it lands
 * on. The number for that is the height of masonry standing between the deck
 * soffit and the ground at the end of the span. If the soffit is BELOW the
 * adjacent ground the deck is buried in the bank and there is no abutment at
 * all, however much of one was drawn.
 *
 * Per deck span, per end:
 *   railhead / soffit at the span end
 *   ground (the terrain that finally built) at the end, and 5/10/15 m beyond
 *   showM = soffit - ground, i.e. how tall the visible abutment is
 * and the fill profile through the span, so a span whose ends were padded out
 * into flat ground by the widening rule can be told from one that was not.
 *
 *   node rr-abut.mjs [--layouts 2]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
}
const layouts = parseInt(a.layouts || '2', 10);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));

let worstAll = Infinity, buried = 0, ends = 0;
let worstDrawn = Infinity, abBuried = 0, abn = 0;
for (let L = 0; L < layouts; L++) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail` +
               `&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`,
               {waitUntil: 'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  await p.waitForTimeout(3000);
  let r = await p.evaluate(() => {
    const w = window.__lemWorld;
    const rail = w.subsystems.get('rail'), terr = w.subsystems.get('terrain');
    const SOFFIT = -0.627 - 0.03 - 1.05;     // BALLAST_TOE - 0.03 - 1.05
    const out = [];
    for (const t of rail.tracks) {
      const f = t.frames; if (!f) continue;
      let ws = []; try { ws = t.earthworks(); } catch { continue; }
      for (const s of ws) {
        if (s.kind !== 'viaduct' && s.kind !== 'bridge') continue;
        const at = i => {
          const q = Math.max(0, Math.min(f.count - 1, i));
          return [f.pos[q * 3], f.pos[q * 3 + 1], f.pos[q * 3 + 2]];
        };
        const rec = {track: t.name, kind: s.kind, from: +s.from.toFixed(1),
                     to: +s.to.toFixed(1), len: +(s.to - s.from).toFixed(1),
                     maxDepth: +s.maxDepth.toFixed(1), ends: [], profile: []};
        for (const [nm, i, dir] of [['from', s.i0, -1], ['to', s.i1, +1]]) {
          const P = at(i);
          const soff = P[1] + SOFFIT;
          const g0 = terr.heightAt(P[0], P[2]);
          const beyond = [5, 10, 15].map(d => {
            const q = Math.max(0, Math.min(f.count - 1, i + dir * Math.round(d / f.step)));
            return +terr.heightAt(f.pos[q * 3], f.pos[q * 3 + 2]).toFixed(1);
          });
          /* rail's OWN sampled ground at declaration time, against the ground
           * terrain finally built. If these differ the span was classified on
           * one landform and drawn on another, and no amount of drawing fixes
           * it from this side. */
          const gDecl = t.groundY ? t.groundY[Math.max(0, Math.min(t.groundY.length - 1, i))] : null;
          rec.ends.push({end: nm, railhead: +P[1].toFixed(2), soffit: +soff.toFixed(2),
                         ground: +g0.toFixed(2), showM: +(soff - g0).toFixed(2),
                         declaredGround: gDecl === null ? null : +gDecl.toFixed(2),
                         declaredDepth: gDecl === null ? null : +(P[1] - 0.687 - gDecl).toFixed(2),
                         groundBeyond: beyond});
        }
        for (let q = s.i0; q <= s.i1; q += Math.max(1, Math.round(4 / f.step))) {
          const P = at(q);
          rec.profile.push(+(P[1] + SOFFIT - terr.heightAt(P[0], P[2])).toFixed(1));
        }
        out.push(rec);
      }
    }
    /* Where the abutment is actually DRAWN, which after rail learned to
     * re-seat its structures is not the end of the declared span. rail
     * publishes the point; the ground under it comes from terrain. */
    const ab = (rail.abutments || []).map(a => ({
      track: a.track, s: +a.s.toFixed(1), kind: a.kind,
      span: `${a.spanFrom.toFixed(0)}-${a.spanTo.toFixed(0)}`,
      soffit: +a.soffit.toFixed(2),
      ground: +terr.heightAt(a.x, a.z).toFixed(2),
      showM: +(a.soffit - terr.heightAt(a.x, a.z)).toFixed(2),
      base: +a.base.toFixed(2)}));
    return {spans: out, ab, groundFinal: !!rail._groundFinal};
  });
  const ab = r.ab, groundFinal = r.groundFinal;
  r = r.spans;
  console.log(`\n=== layout ${L}: ${r.length} deck spans`);
  for (const s of r) {
    console.log(`  ${s.track} ${s.kind} ${s.from}-${s.to} (${s.len}m, declared maxDepth ${s.maxDepth}m)`);
    for (const e of s.ends) {
      ends++;
      if (e.showM < worstAll) worstAll = e.showM;
      if (e.showM < 0.5) buried++;
      console.log(`     ${e.end.padEnd(4)} railhead ${e.railhead}  soffit ${e.soffit}  ` +
                  `ground ${e.ground}  ABUTMENT SHOWS ${e.showM} m   ` +
                  `[rail sampled ground ${e.declaredGround}, so declared fill ${e.declaredDepth} m]  ` +
                  `ground 5/10/15m beyond ${JSON.stringify(e.groundBeyond)}`);
    }
    console.log(`     soffit above ground through the span, every 4m: ${JSON.stringify(s.profile)}`);
  }
  console.log(`  --- abutments as DRAWN (rail._groundFinal = ${groundFinal}) ---`);
  for (const a of ab) {
    abn++;
    if (a.showM < worstDrawn) worstDrawn = a.showM;
    if (a.showM < 1.0) abBuried++;
    console.log(`     ${a.track} ${a.kind} span ${a.span} -> abutment at s=${a.s}  ` +
                `soffit ${a.soffit}  ground ${a.ground}  ABUTMENT SHOWS ${a.showM} m`);
  }
}
console.log(`\nTOTAL declared span ends: ${ends}, ${buried} with under 0.5m showing; ` +
            `worst ${worstAll.toFixed(2)} m`);
console.log(`TOTAL abutments as drawn: ${abn}, ${abBuried} showing under 1.0 m; ` +
            `worst ${worstDrawn.toFixed(2)} m`);
await b.close();
