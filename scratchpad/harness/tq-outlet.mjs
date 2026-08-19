/* tq-outlet.mjs — does the drainage network REACH THE SEA?
 *
 * The standing charge is "rain falling on this island has nowhere to go … not
 * one low line connects the interior to the coast; the beach is an unbroken
 * barrier with no outlet". `tq-form`'s pctWatercourse says how much channel
 * exists; it says nothing about where the channel ENDS, and a network that
 * fades out ten metres above the waterline scores identically to one that cuts
 * the strand.
 *
 * So this walks the shoreline and asks, at every bearing:
 *
 *   flowShore   the drainage accumulation AT the waterline crossing
 *   notch       how far the ground at the shore sits BELOW the mean of the
 *               ground either side of it along the shore, at +-30 m — i.e. is
 *               there a physical gap in the beach here, or only a wet stripe
 *   outlets     bearings whose flowShore clears the `stream` threshold
 *
 * and then, for the strongest outlets, walks the channel inland and reports its
 * depth profile against the same azimuthal-mean reference tq-form uses, so a
 * "channel" that is really just the coast profile cannot score.
 *
 *   node tq-outlet.mjs [--bearings 360]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const NB = +(a.bearings || 360);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errors = [];
p.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=wide&time=9&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3500);

const out = await p.evaluate(({NB}) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const cx = t.cx, cz = t.cz, wy = t.waterY;

  /* the waterline, by bisection on the graded height along each bearing */
  const shore = [];
  for (let k = 0; k < NB; k++) {
    const th = (k / NB) * Math.PI * 2, ux = Math.cos(th), uz = Math.sin(th);
    let lo = 0, hi = 0;
    for (let r = 60; r < 2600; r += 6) {
      if (t._gradedHeight(cx + ux * r, cz + uz * r) <= wy) { hi = r; lo = r - 6; break; }
    }
    if (!hi) continue;
    for (let it = 0; it < 24; it++) {
      const m = (lo + hi) / 2;
      if (t._gradedHeight(cx + ux * m, cz + uz * m) <= wy) hi = m; else lo = m;
    }
    shore.push({k, th, ux, uz, r: lo, x: cx + ux * lo, z: cz + uz * lo});
  }

  /* flow at the shore, and just inland of it — the mouth is where the channel
   * crosses, and a channel a few metres wide on a 10 m grid is easiest to
   * catch by taking the strongest sample over a short inland run */
  for (const s of shore) {
    let f = 0, fAt = 0;
    for (let d = 0; d <= 40; d += 4) {
      const v = t._flowAt(s.x - s.ux * d, s.z - s.uz * d);
      if (v > f) { f = v; fAt = d; }
    }
    s.flowShore = +f.toFixed(3);
    s.flowAt = fAt;
  }

  /* the NOTCH: ground height at the shore radius, against the same radius on
   * the bearings either side. A mouth is a gap in the beach, so the test is
   * whether the land is lower HERE than beside it at the same distance out. */
  const W = Math.max(2, Math.round(NB / 72));      // ~+-5 deg either side
  for (let n = 0; n < shore.length; n++) {
    const s = shore[n];
    /* how far the shoreline itself is pushed inland relative to its
     * neighbours: a channel mouth cuts a re-entrant in the coast */
    let sum = 0, c = 0;
    for (let d = -W * 3; d <= W * 3; d++) {
      if (Math.abs(d) < W) continue;
      const o = shore[(n + d + shore.length) % shore.length];
      sum += o.r; c++;
    }
    s.reentrant = +(sum / c - s.r).toFixed(2);     // +ve = shore is inland here

    /* and the depth of the ground 30 m inland against its neighbours at the
     * same radius — the channel bed itself */
    const rr = s.r - 30;
    const hHere = t._gradedHeight(cx + s.ux * rr, cz + s.uz * rr);
    let hs = 0, hc = 0;
    for (let d = -W * 3; d <= W * 3; d++) {
      if (Math.abs(d) < W) continue;
      const o = shore[(n + d + shore.length) % shore.length];
      hs += t._gradedHeight(cx + o.ux * rr, cz + o.uz * rr); hc++;
    }
    s.notch = +(hs / hc - hHere).toFixed(2);       // +ve = lower here = a notch
  }

  const sorted = shore.slice().sort((x, y) => y.flowShore - x.flowShore);
  const nOut = (th) => shore.filter(s => s.flowShore > th).length;

  /* ---- the profile of the best outlets, walked inland -------------------- */
  const profiles = sorted.slice(0, 5).map(s => {
    const prof = [];
    for (let d = -40; d <= 260; d += 20) {
      const x = s.x - s.ux * d, z = s.z - s.uz * d;
      prof.push({d, h: +t._gradedHeight(x, z).toFixed(2),
                 f: +t._flowAt(x, z).toFixed(2)});
    }
    return {bearingDeg: +(s.th * 180 / Math.PI).toFixed(1), r: +s.r.toFixed(0),
            flowShore: s.flowShore, notch: s.notch, reentrant: s.reentrant, prof};
  });

  /* how continuous is the strongest channel — does it run from high ground to
   * the water without a break, or is it a disconnected patch near the coast? */
  const best = sorted[0];
  let runFrom = null, runTo = null, broken = 0;
  if (best) {
    for (let d = 0; d <= 400; d += 10) {
      const f = t._flowAt(best.x - best.ux * d, best.z - best.uz * d);
      if (f > 0.20) { if (runFrom === null) runFrom = d; runTo = d; }
      else if (runFrom !== null && d - runTo <= 30) broken++;
    }
  }

  const fs = shore.map(s => s.flowShore).sort((x, y) => x - y);
  const q = pp => fs.length ? +fs[Math.min(fs.length - 1, Math.floor(pp * fs.length))].toFixed(3) : 0;

  return {
    bearings: shore.length, waterY: +wy.toFixed(2),
    flowShore: {p50: q(0.5), p90: q(0.9), p99: q(0.99),
                max: fs.length ? fs[fs.length - 1] : 0},
    outlets: {gt20: nOut(0.20), gt40: nOut(0.40), gt55: nOut(0.55),
              pctGt40: +(100 * nOut(0.40) / shore.length).toFixed(1)},
    notch: {mean: +(shore.reduce((s2, s) => s2 + s.notch, 0) / shore.length).toFixed(2),
            atOutlets: +(shore.filter(s => s.flowShore > 0.40)
                          .reduce((s2, s) => s2 + s.notch, 0)
                        / Math.max(1, nOut(0.40))).toFixed(2)},
    bestChannel: best ? {bearingDeg: +(best.th * 180 / Math.PI).toFixed(1),
                         reachesInlandM: runTo, startsAtM: runFrom, breaks: broken} : null,
    profiles,
  };
}, {NB});

out.pageErrors = errors;
console.log(JSON.stringify(out, null, 1));
await b.close();
