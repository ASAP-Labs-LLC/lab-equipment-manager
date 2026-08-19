/* giaccept.mjs — the no-GI rung's acceptance, both tiers on ONE world.
 *
 * Two things this round needed that no existing harness does.
 *
 * First: the same world for both frames. `terrain.js` is being rewritten in the
 * same hour and its build intermittently throws, falling back to a bare plane —
 * so two separate page loads can and did give one frame with a forested site in
 * it and one with a mirror. Comparing a lighting tier against itself across
 * that is worthless. This loads once, shoots `ultra`, steps the engine down to
 * `floor` in place, and shoots again.
 *
 * Second: a gate on whether the site actually built. A fallback plane still
 * reports a million triangles (vegetation is standing on it) and no page error
 * (terrain catches its own), so the only honest signal is the draw count — the
 * real site is ~250, the fallback ~140. It retries until it gets one.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/r8-accept');
const CAM = args.cam || 'yard';
const TIME = args.time || '16';
const MIN_DRAWS = parseInt(args.minDraws || '200', 10);
const TRIES = parseInt(args.tries || '25', 10);
fs.mkdirSync(OUT, {recursive: true});

const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${CAM}&time=${TIME}` +
  `&weather=${args.weather || 'clear'}&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});

const measure = async page => page.evaluate(async () => {
  const w = window.__lemWorld;
  const eng = w.engine;
  const gi = w.subsystems.get('gi');
  const ms = [];
  let last = performance.now();
  await new Promise(res => {
    let n = 0;
    const tick = () => {
      const now = performance.now(); ms.push(now - last); last = now;
      if (++n >= 240) return res();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  ms.sort((a, b) => a - b);
  const info = eng.renderer.info.render;
  return {
    tier: eng.tier.name,
    draws: info.calls, tris: info.triangles,
    fps: +(1000 / ms[Math.floor(ms.length * 0.5)]).toFixed(1),
    p95ms: +ms[Math.floor(ms.length * 0.95)].toFixed(2),
    gi: {
      flat: gi._flat, budget: gi._budget,
      grid: !!gi.grid, probes: gi.grid ? gi.grid.count : 0,
      cascades: gi._csm.length, pool: gi._pool.length,
      sunCasts: !!gi.sun?.castShadow,
      env: !!w.scene.environment, flatSpec: gi.uniforms.lemFlatSpec.value,
      emissiveGain: gi.uniforms.lemEmissiveGain.value,
      giStrength: +gi.uniforms.lemGIStrength.value.toFixed(4),
      aoStrength: gi.uniforms.lemAOStrength.value,
      exposure: +(gi.exposure || 0).toFixed(3),
      shadowMapEnabled: eng.renderer.shadowMap.enabled,
    },
  };
});

for (let attempt = 1; attempt <= TRIES; attempt++) {
  const ctx = await browser.newContext({viewport: {width: 1920, height: 1080}});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
  page.on('console', m => {
    const t = m.text();
    if (/Failed to load resource/.test(t)) return;   // a missing favicon is not a render fault
    if (m.type() === 'error' || /build failed|did not load/.test(t)) {
      errors.push(t.slice(0, 160));
    }
  });
  try {
    await page.goto(url, {waitUntil: 'load', timeout: 60000});
    await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
    /* Let a few trains out, so both frames have rolling stock in them. */
    await page.evaluate(() => {
      const w = window.__lemWorld;
      const uids = w.plan.stations.map(s => s.uid);
      let i = 0;
      setInterval(() => w.parse(uids[i++ % uids.length], 'L-ACC'), 900);
    });
    await page.waitForTimeout(6000);

    const a = await measure(page);
    /* Terrain must have LOADED, not merely not crashed the page. Its build
     * catches its own failure and the site keeps ~330 draw calls without it
     * (buildings, rail, trains and vegetation are most of them), so a draw-count
     * gate passes a frame with no ground in it — which is exactly the frame this
     * round must not be judged on. */
    const built = await page.evaluate(() => {
      const w = window.__lemWorld;
      const t = w.subsystems.get('terrain');
      return {terrain: !!t,
              ground: t ? Math.abs(t.heightAt?.(40, 40) ?? 0) +
                          Math.abs(t.heightAt?.(-160, 220) ?? 0) : 0};
    });
    /* `built.ground` is the clincher: the fallback is a flat plane, so a site
     * whose height function answers 0 everywhere did not build whatever the
     * subsystem map says. */
    if (a.draws < MIN_DRAWS || !built.terrain || built.ground < 1 || errors.length) {
      console.log(`try ${attempt}: site not ready (draws ${a.draws}, ` +
                  `terrain ${built.terrain} ground ${built.ground.toFixed(1)}) ` +
                  JSON.stringify(errors));
      await ctx.close();
      await new Promise(r => setTimeout(r, 20000));
      continue;
    }
    await page.screenshot({path: path.join(OUT, 'ultra.png')});

    await page.evaluate(() => window.__lemWorld.engine.setQualityMode('floor'));
    await page.waitForTimeout(6000);
    const b = await measure(page);
    await page.screenshot({path: path.join(OUT, 'floor.png')});

    /* And back up again, in the same session — the ladder climbs out of the
     * floor tier in production and the shadow flags have to come back with it.
     * A frame that only ever goes downhill would never have caught that. */
    await page.evaluate(() => window.__lemWorld.engine.setQualityMode('ultra'));
    await page.waitForTimeout(6000);
    const c = await measure(page);
    await page.screenshot({path: path.join(OUT, 'ultra-again.png')});
    const casters = await page.evaluate(() => {
      let n = 0;
      window.__lemWorld.scene.traverse(o => { if (o.castShadow && o.isObject3D && !o.isLight) n++; });
      return n;
    });

    console.log(JSON.stringify({attempt, errors, ultra: a, floor: b,
                                ultraAgain: c, castersAfterClimb: casters}, null, 1));
    await ctx.close();
    await browser.close();
    process.exit(0);
  } catch (e) {
    console.log(`try ${attempt}: ${String(e).slice(0, 120)}`);
    await ctx.close();
  }
}
await browser.close();
process.exit(1);
