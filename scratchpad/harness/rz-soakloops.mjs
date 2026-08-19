/* soak.mjs — drive the world hard and assert it never does the impossible.
 *
 *   node soak.mjs [--parses 500] [--layouts 10] [--headed] [--json out.json]
 *
 * The visual gauntlet judged pictures. This judges behaviour, because the
 * defects Ryan reported are not things a screenshot shows: trains running into
 * each other, trains reversing back through the railway, buildings standing on
 * ground that was never generated for them, stations the railway cannot reach,
 * and a map that stops at a cliff.
 *
 * Five assertions, checked across randomised machine layouts:
 *
 *   collision      two consists occupying the same piece of track, or two
 *                  consists closer together than they are long
 *   reversal       a working travelling backwards along its own path for a
 *                  sustained period, rather than round the loop
 *   floating       a building whose base does not sit on the terrain height
 *                  underneath it
 *   unreachable    a station the rail network cannot route to
 *   edge           a discontinuity in ground height at the map boundary — the
 *                  "massive lip" — found by walking a ray outward and looking
 *                  for a step no landscape would have
 *
 * The per-frame work happens IN THE PAGE. Sampling 500 parses over a
 * WebSocket, one round trip per frame, would take longer than the run and
 * would miss the frame where two trains actually touch.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}

const PARSES = parseInt(args.parses || '500', 10);
const LAYOUTS = parseInt(args.layouts || '10', 10);
const HEADED = !!args.headed;
const PER_LAYOUT = Math.max(1, Math.round(PARSES / LAYOUTS));

/* The lab's real instruments. Positions are replaced per layout; the uids and
 * titles stay, because a machine keeping its identity across a rearrangement is
 * part of what is being tested. */
const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];

/* Deterministic layouts, so a failing run can be reproduced exactly. Layout 0
 * is the lab's real arrangement; the rest are progressively more awkward,
 * because "logics its way through any layout" is the actual requirement —
 * a rail network that only works on a tidy grid has not been solved. */
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
      else if (kind === 1) pos.push([i * BAY, 0]);                       // one long rank
      else if (kind === 2) pos.push([0, i * BAY]);                       // one long file
      else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]); // sparse
    }
    // Collisions on a bay are legal input — the floor spills them — so leave
    // duplicates in deliberately on one layout.
    if (kind === 3) pos[1] = pos[0].slice();
    all.push(pos);
  }
  return all;
}

const SAMPLER = () => {
  const w = window.__lemWorld;
  const faults = [];
  const seen = new Set();
  const note = (kind, detail) => {
    const key = kind + '|' + detail;
    if (seen.has(key)) return;          // one report per distinct fault
    seen.add(key);
    faults.push({kind, detail, at: Math.round(performance.now())});
  };
  const back = new Map();               // consist slot -> frames going backwards
  const lastS = new Map();              // consist slot -> previous distance along route
  /* A frozen railway breaks none of the safety rules. Zero collisions on a
   * network where nothing moves is a pass for all the wrong reasons, and that
   * is exactly how a deadlock survived this gate once already: a working stood
   * holding eleven trunk blocks for ever because a floating-point comparison
   * missed by 2.27e-13, and every counter stayed clean. So the soak also has to
   * assert the thing actually runs. */
  window.__soakStats = {maxConcurrent: 0, everOut: 0, backwardsFrames: 0,
                        junctionUnchecked: 0, junctionChecked: 0,
                        arrivals: 0, distance: 0, longestStand: 0,
                        states: {}, transitions: []};
  const prevState = new Map();
  const prevAny = new Map();
  const anyS = new Map();
  const standing = new Map();

  /* Where a consist actually is, in world metres.
   *
   * An earlier version of this harness reported that route objects "expose no
   * way to sample a point" and disabled the cross-line check on that basis.
   * That was wrong: it probed for getPointAt / getLength / totalLength, found
   * none, and concluded absence. The routes carry `pointAtDistance(s)`, plus
   * `points`, `acc` and `length`. Concluding a capability is missing from three
   * guessed property names is how a check ends up switched off for no reason. */
  /* Sample a consist's BODY, not just its head. Two workings can foul at a
   * throat with their heads more than the clearance apart — the head-only test
   * was reported as a gap by the interlocking work and it is a real one. */
  const bodyAt = c => {
    const pts = [];
    for (const frac of [0, 0.5, 1]) {
      const s2 = c.s - c.length * frac;
      if (s2 < 0) continue;
      const q = at({route: c.route, s: s2});
      if (q) pts.push(q);
    }
    return pts;
  };

  const at = c => {
    try {
      const r = c.route;
      if (!r) return null;
      if (typeof r.pointAtDistance === 'function') return r.pointAtDistance(c.s);
      /* Resolve the length from whatever the route calls it. `r.length` is NOT
       * it — on the object a consist carries, the length lives in `totalLength`
       * / `len` / `getLength()`, and reading `r.length` gets undefined, which
       * silently disabled this whole check once already. */
      const len = r.totalLength || r.len ||
                  (typeof r.getLength === 'function' ? r.getLength() : 0);
      if (typeof r.getPointAt === 'function' && len)
        return r.getPointAt(Math.min(1, Math.max(0, c.s / len)));
      if (typeof r.getPoint === 'function' && len)
        return r.getPoint(Math.min(1, Math.max(0, c.s / len)));
      return null;
    } catch (e) { return null; }
  };

  const tick = () => {
    const T = w.subsystems && w.subsystems.get('trains');
    if (T && Array.isArray(T.consists)) {
      /* A PARKED train still occupies track.
       *
       * This filter used to exclude `state === 'idle'`, which quietly excused
       * the exact fault the signalling audit then reproduced by hand: a working
       * leaving its stand drove through its idle neighbour at 0.0045m. An
       * interlocking that cannot see a stationary train is not an interlocking,
       * and a harness that cannot see one is not a test. Anything on the rail
       * with a route counts, moving or not. */
      const live = T.consists.filter(c => c && c.group && c.group.visible &&
                                          c.route);
      const st = window.__soakStats;
      if (live.length > st.maxConcurrent) st.maxConcurrent = live.length;

      /* Throughput is measured over EVERY consist, not just the ones the
       * collision check looks at. `live` is filtered to visible consists,
       * and a working is not visible while it is discharging — so counting
       * arrivals off that list filtered out the very state it was looking
       * for and reported a healthy railway as dead. Safety checks want the
       * visible set; liveness wants all of them. */
      for (const c of T.consists) {
        if (!c) continue;
        st.states[c.state] = (st.states[c.state] || 0) + 1;
        const wasAny = prevAny.get(c.slot);
        if (wasAny !== undefined && wasAny !== c.state) {
          if (st.transitions.length < 30) st.transitions.push(`${wasAny}->${c.state}`);
          if (/discharge|unload|arriv/i.test(String(c.state))) st.arrivals++;
        }
        prevAny.set(c.slot, c.state);
        const pS = anyS.get(c.slot);
        if (pS !== undefined && c.s > pS) st.distance += (c.s - pS);
        anyS.set(c.slot, c.s);
      }
      /* Collision, two ways. On one line, compare occupied intervals along the
       * route. Across lines, compare world positions — a shared junction does
       * not share a route object. */
      for (let i = 0; i < live.length; i++) {
        for (let j = i + 1; j < live.length; j++) {
          const a = live[i], b = live[j];
          /* Gate on the LINE, not on route identity. Each consist carries its
           * own route object even when working the same line, so comparing
           * `a.route === b.route` skipped every real case — the first version
           * of this harness reported zero collisions while slots 0 and 3 sat
           * 2.8m apart on line0 with 24m consists. */
          if (a.line && a.line === b.line) {
            const aS = a.s - a.length, aE = a.s;
            const bS = b.s - b.length, bE = b.s;
            if (aS < bE - 0.5 && bS < aE - 0.5) {
              note('collision', `slots ${a.slot}/${b.slot} overlap on one line ` +
                   `(${aS.toFixed(1)}..${aE.toFixed(1)} vs ${bS.toFixed(1)}..${bE.toFixed(1)})`);
            }
          }
          /* Two lines fouling each other at a shared junction. The interval
           * test above cannot see this — the workings are on different lines,
           * so their distances-along-route are not comparable — and it is
           * exactly where a real railway needs a chain signal. */
          const A = bodyAt(a), B = bodyAt(b);
          if (A.length && B.length) {
            window.__soakStats.junctionChecked++;
            let d = Infinity;
            for (const pa of A) for (const pb of B) {
              d = Math.min(d, Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z));
            }
            /* Lateral fouling, not following distance. Half a consist length is
             * the right rule for two workings nose-to-tail on ONE line, and the
             * interval test above already applies it there. Applied across
             * lines it is simply wrong: trains on adjacent roads pass within a
             * few metres as a matter of routine, and using 26m flagged two
             * workings on diverging branches at 23m as a collision. What fouls
             * is physical overlap — track centres about 4m apart plus a body
             * about 3m wide, so anything under ~5m is two vehicles in the same
             * space and anything above it is a normal passing move. */
            const FOUL = 5;
            if (d < FOUL) {
              note('collision', `slots ${a.slot}/${b.slot} ${d.toFixed(1)}m apart ` +
                   `across ${a.line}/${b.line} — fouling (under ${FOUL}m)`);
            }
          } else {
            window.__soakStats.junctionUnchecked++;
          }
        }
      }
      /* Reversal. Running backwards along its own path is different from
       * working round the loop, which advances `s` the whole way. A few frames
       * of negative velocity is a stop; half a second of it is a train going
       * the wrong way. */
      for (const c of live) {
        if (c.state === 'idle') { lastS.set(c.slot, c.s); continue; }
        const prev = lastS.get(c.slot);
        if (prev !== undefined && c.s < prev - 0.25 && c.dwell <= 0 && c.state !== 'turn') {
          window.__soakStats.backwardsFrames++;
          const n = (back.get('s' + c.slot) || 0) + 1;
          back.set('s' + c.slot, n);
          if (n === 20) {
            note('reversal', `slot ${c.slot} wound BACK along its route ` +
                 `(${prev.toFixed(1)} → ${c.s.toFixed(1)}, dir=${c.dir}, state=${c.state})`);
          }
        } else if (prev !== undefined && c.s > prev) {
          back.set('s' + c.slot, 0);
        }
        /* Throughput: an arrival is a working reaching the terminal and
         * beginning to discharge. */
        if (prev !== undefined && c.s > prev) {
          standing.set(c.slot, 0);
        } else if (c.dwell <= 0) {
          const t = (standing.get(c.slot) || 0) + 1;
          standing.set(c.slot, t);
          if (t > window.__soakStats.longestStand) {
            window.__soakStats.longestStand = t;
          }
        }
        lastS.set(c.slot, c.s);
        if (c.v < -0.15 && c.dwell <= 0) {
          const n = (back.get(c.slot) || 0) + 1;
          back.set(c.slot, n);
          if (n === 30) {
            note('reversal', `slot ${c.slot} ran backwards ~0.5s at s=${c.s.toFixed(1)} ` +
                 `(v=${c.v.toFixed(2)}, dir=${c.dir}, state=${c.state})`);
          }
        } else {
          back.set(c.slot, 0);
        }
      }
    }
    window.__soakFaults = faults;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  window.__soakArmed = true;
};

/* Structural checks — run once per layout, after the world has re-planned. */
const STRUCTURE = () => {
  const w = window.__lemWorld;
  const out = [];
  const plan = w.plan;
  const terrain = w.subsystems.get('terrain');
  const rail = w.subsystems.get('rail');
  const buildings = w.subsystems.get('buildings');
  if (!plan) return [{kind: 'plan', detail: 'no plan after setMachines'}];

  for (const st of plan.stations) {
    // Reachability: the railway must be able to route from this station.
    let route = null;
    try { route = rail && rail.route ? rail.route(st.uid) : null; } catch (e) {
      out.push({kind: 'unreachable', detail: `${st.uid}: route() threw ${e.message}`});
    }
    if (!route) out.push({kind: 'unreachable', detail: `${st.uid} has no route to the terminal`});

    // Conforming ground: the building's base must sit on the terrain.
    const gy = terrain && terrain.heightAt ? terrain.heightAt(st.x, st.z) : null;
    const anchor = w.anchors && w.anchors.get ? w.anchors.get(st.uid) : null;
    const dock = buildings && buildings.dock ? buildings.dock(st.uid) : null;
    const baseY = dock && dock.position ? dock.position.y : (anchor ? anchor.baseY : null);
    if (gy === null) out.push({kind: 'floating', detail: `${st.uid}: no terrain height`});
    else if (baseY !== null && baseY !== undefined && Math.abs(baseY - gy) > 2.5) {
      out.push({kind: 'floating', detail:
        `${st.uid} base ${baseY.toFixed(2)} vs ground ${gy.toFixed(2)} ` +
        `(${(baseY - gy).toFixed(2)}m off)`});
    }

    /* The dock point alone is not the check Ryan asked for. "Buildings need to
     * have the terrain beneath them generate" is about the PAD: the ground has
     * to be cut level all the way round the building, not just true at the one
     * spot the building is anchored to. A single centre sample passes happily
     * while the far side of the shed hangs over a slope — which is exactly what
     * a fine field that stops before the site does. So sample a ring at 12m and
     * 24m: the pad is a plane, and the design plane's own gradient is capped at
     * 1.8% per axis, so nothing on it may deviate by more than ~0.9m plus mesh
     * sampling. Two metres is generous and still catches a building standing on
     * a hillside. */
    if (gy !== null && isFinite(gy)) {
      let worst = 0, at = null;
      for (const r of [12, 24]) {
        for (let a = 0; a < 8; a++) {
          const ang = (a / 8) * Math.PI * 2;
          const h = terrain.heightAt(st.x + Math.cos(ang) * r, st.z + Math.sin(ang) * r);
          if (!isFinite(h)) { worst = Infinity; at = `r=${r} bearing ${a} → ${h}`; break; }
          if (Math.abs(h - gy) > Math.abs(worst)) { worst = h - gy; at = `r=${r} bearing ${a}`; }
        }
      }
      if (Math.abs(worst) > 2.0) {
        out.push({kind: 'floating', detail:
          `${st.uid} pad is not cut: ground ${worst.toFixed(2)}m off the dock at ${at}`});
      }
    }
  }

  /* The map edge. Walk outward along several bearings and look for a step no
   * landscape would have — that is the "massive lip". */
  if (terrain && terrain.heightAt) {
    const cx = (plan.bounds.minX + plan.bounds.maxX) / 2;
    const cz = (plan.bounds.minZ + plan.bounds.maxZ) / 2;
    for (let b = 0; b < 8; b++) {
      const a = (b / 8) * Math.PI * 2;
      let prev = terrain.heightAt(cx, cz);
      for (let r = 20; r < 4000; r += 20) {
        const x = cx + Math.cos(a) * r, z = cz + Math.sin(a) * r;
        const h = terrain.heightAt(x, z);
        if (!isFinite(h)) {
          out.push({kind: 'edge', detail: `height is ${h} at r=${r}m bearing ${b}`});
          break;
        }
        if (Math.abs(h - prev) > 26) {
          out.push({kind: 'edge', detail:
            `${(h - prev).toFixed(1)}m step at r=${r}m bearing ${b} ` +
            `(${prev.toFixed(1)} → ${h.toFixed(1)})`});
          break;
        }
        prev = h;
      }
    }
  }
  return out;
};

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0`;

const browser = await chromium.launch({headless: !HEADED, channel: HEADED ? undefined : 'chromium',
                                       args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const consoleErrors = [];
page.on('pageerror', e => consoleErrors.push(String(e).slice(0, 200)));
/* The app serves no favicon, so Chromium's automatic request 404s on every run
 * and Chromium's console line for a 404 does not name the file. Filtering the
 * bare line is safe because the two handlers below DO name it, so a genuinely
 * missing module still lands with its URL. */
const NOISE = /favicon\.ico|the server responded with a status of 404 \(NOT FOUND\)$/;
page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text()))
  consoleErrors.push(m.text().slice(0, 200)); });
page.on('requestfailed', r => { if (!/favicon\.ico/.test(r.url()))
  consoleErrors.push('request failed: ' + r.url()); });
page.on('response', r => { if (r.status() >= 400 && !/favicon\.ico/.test(r.url()))
  consoleErrors.push('HTTP ' + r.status() + ': ' + r.url()); });

await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);
await page.evaluate(SAMPLER);

/* ---- rz-soakloops: the passing loops FORCED ON ---------------------------
 *
 * trains.js does not consume `rail.cycle().variants` — it built the
 * consumption, measured it, and took it out again when the soak went to
 * `collision: 10`. So `soak.mjs` PASSING today proves nothing about the
 * loops: nothing is using them.
 *
 * This is soak.mjs with one page-side patch. `rail.cycle(uid)` is wrapped so
 * the record it hands back IS a variant — a crossover exit rather than the
 * full lap — while still carrying `variants`, so everything that walks them
 * (`_analyseBlocks`, `oneWayReport`) is unaffected. trains.js then samples a
 * crossover circuit without knowing it, which is exactly the traffic the
 * consumption produced.
 *
 *   --variant first   every working takes the earliest exit
 *   --variant mix     alternate exits and full laps (default) — this is the
 *                     adversarial one, because a working on a crossover and a
 *                     working on a full lap is what puts two of them level
 *                     with each other at a throat.
 */
const VARIANT = args.variant || 'mix';
const NOCOUPLE = !!args.nocouple;
async function forceLoops(page, mode) {
  await page.evaluate(v => { window.__noCouple = v; }, NOCOUPLE);
  return page.evaluate(mode => {
    const rail = window.__lemWorld.subsystems.get('rail');
    if (!rail || rail.__loopsForced) return 'already';
    const raw = rail.cycle.bind(rail);
    let n = 0;
    const stats = {calls: 0, variantsUsed: 0, lines: {}};
    rail.cycle = uid => {
      const base = raw(uid);
      stats.calls++;
      const vs = base && base.variants;
      if (!vs || vs.length < 2) return base;
      let pick;
      if (mode === 'first') pick = vs[0];
      else pick = vs[n++ % vs.length];
      if (!pick || pick === base) return base;
      stats.variantsUsed++;
      stats.lines[pick.line] = (stats.lines[pick.line] || 0) + 1;
      /* the variant is a complete cycle record; it needs to carry the list on
       * so nothing downstream loses the other exits */
      if (!pick.variants) pick.variants = vs;
      return pick;
    };
    /* --nocouple: the ABLATION. `_coupleThroats` is what makes a turnout one
     * block across both its roads; wrapping `_sectionBlocks` to strip every
     * `id` afterwards leaves the overlaps correctly sized and the coupling
     * gone, on the same page, the same layouts and the same traffic. */
    if (window.__noCouple) {
      const sb = rail._sectionBlocks.bind(rail);
      rail._sectionBlocks = () => {
        sb();
        for (const [, list] of rail._sections) for (const s of list) delete s.id;
      };
      rail._sectionBlocks();
      rail._analyseBlocks && rail._analyseBlocks();
    }
    rail.__loopsForced = true;
    window.__loopStats = stats;
    /* trains caches its sampled copies, so they have to go */
    const T = window.__lemWorld.subsystems.get('trains');
    if (T && T.cycles) T.cycles.clear();
    return 'patched';
  }, mode);
}
console.log('# loops forced: ' + await forceLoops(page, VARIANT));


const report = {parses: 0, layouts: 0, faults: [], structure: [], consoleErrors: [],
                started: new Date().toISOString()};
const LAYOUT_SET = layouts(LAYOUTS);

for (let L = 0; L < LAYOUT_SET.length; L++) {
  const positions = LAYOUT_SET[L];
  const ok = await page.evaluate(([fleet, pos]) => {
    const list = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'soak',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    try { window.__lemWorld.setMachines(list); return true; }
    catch (e) { return 'setMachines threw: ' + e.message; }
  }, [FLEET, positions]);
  await page.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    if (T && T.cycles) T.cycles.clear();      // re-sample off the patched cycle()
  });
  if (ok !== true) {
    report.structure.push({layout: L, kind: 'relayout', detail: String(ok)});
    continue;
  }
  await page.waitForTimeout(2500);          // let the world re-plan and settle

  const struct = await page.evaluate(STRUCTURE);
  for (const s of struct) report.structure.push({layout: L, ...s});

  /* Parses, faster than the lab would ever produce them — the point is to make
   * the road busy enough that any missing interlock shows. */
  for (let p = 0; p < PER_LAYOUT; p++) {
    await page.evaluate(uid => window.__lemWorld.parse(uid, 'L-SOAK'),
                        FLEET[p % FLEET.length][0]);
    report.parses++;
    await page.waitForTimeout(120);
  }
  /* Let the railway actually work before judging it. A journey is ~1160m and
   * takes the better part of half a minute; firing 50 parses in six seconds and
   * then waiting four proves only that nothing crashed in six seconds. The
   * throughput assertion below is meaningless without this. */
  /* Longer than one journey. Measured: a working covers ~1160m and takes the
   * better part of 40 seconds, so a 30s settle ended every layout with the
   * lead train still short of the terminal and the throughput assertion
   * reporting a healthy railway as dead. */
  await page.waitForTimeout(52000);
  report.layouts++;
  const faults = await page.evaluate(() => window.__soakFaults || []);
  process.stdout.write(`layout ${L}: ${report.parses} parses, ` +
    `${faults.length} movement faults, ${struct.length} structural\n`);
}

report.faults = await page.evaluate(() => window.__soakFaults || []);
report.stats = await page.evaluate(() => window.__soakStats || {});
report.consoleErrors = consoleErrors.slice(0, 20);
const LOOPSTATS = await page.evaluate(() => window.__loopStats);
await browser.close();

const tally = k => report.faults.filter(f => f.kind === k).length;
const stally = k => report.structure.filter(f => f.kind === k).length;
report.summary = {
  parses: report.parses, layouts: report.layouts,
  collision: tally('collision'), reversal: tally('reversal'),
  floating: stally('floating'), unreachable: stally('unreachable'),
  edge: stally('edge'), relayout: stally('relayout'),
  consoleErrors: report.consoleErrors.length,
};
const st = report.stats || {};
/* A railway that carried nothing is a failure even with every safety counter
 * at zero — see the deadlock note in the sampler. */
report.summary.arrivals = st.arrivals || 0;
report.summary.metresRun = Math.round(st.distance || 0);
const deadRailway = (st.arrivals || 0) < report.layouts ||
                    (st.distance || 0) < report.parses * 20;
report.summary.deadRailway = deadRailway ? 1 : 0;
report.pass = Object.entries(report.summary)
  .filter(([k]) => !['parses', 'layouts', 'arrivals', 'metresRun'].includes(k))
  .every(([, v]) => v === 0);

console.log('# loopStats ' + JSON.stringify(LOOPSTATS));
console.log('\n=== SOAK ===');
console.log('traffic:', JSON.stringify(report.stats));
console.log(JSON.stringify(report.summary, null, 1));
console.log(report.pass ? 'PASS' : 'FAIL');
for (const f of report.faults.slice(0, 12)) console.log(`  ${f.kind}: ${f.detail}`);
for (const f of report.structure.slice(0, 12)) console.log(`  L${f.layout} ${f.kind}: ${f.detail}`);
if (args.json) fs.writeFileSync(args.json, JSON.stringify(report, null, 2));
process.exit(report.pass ? 0 : 1);
