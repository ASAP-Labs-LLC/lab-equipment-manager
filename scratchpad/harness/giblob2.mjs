/* giblob2.mjs — which contributor paints the moving blob under a consist?
 *
 * Freezes the trains subsystem so the consist stops, then photographs the same
 * frozen frame with one lighting contributor disabled at a time. Whatever
 * removes the dark shape is the mechanism.
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
const OUT = path.resolve(args.out || '../shots/giblob2');
fs.mkdirSync(OUT, {recursive: true});
const MODS = args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=clear&hud=0`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') errors.push(m.text().slice(0, 200)); });

await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

/* Put a consist on the road and let it get well clear of the station, then
 * stop the clock on the trains subsystem alone. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.parse(w.plan.stations[2].uid, 'L-BLOB');
});
await page.waitForTimeout(parseInt(args.roll || '5000', 10));

const info = await page.evaluate(() => {
  const w = window.__lemWorld;
  const tr = w.subsystems.get('trains');
  if (tr) { tr._frozenUpdate = tr.update; tr.update = () => {}; }
  const gi = w.subsystems.get('gi');
  window.__gi = gi;
  const out = {consists: [], casters: {}};
  try {
    for (const c of (tr?.consists || tr?._consists || [])) {
      out.consists.push({d: c.distance ?? c.dist, speed: c.speed});
    }
  } catch (e) { out.consistErr = String(e); }
  out.csm = (gi._csm || []).map(c => ({i: c.i, size: c.rt?.width, casters: c.casters.length,
    radius: c.radius, ready: gi.uniforms[`lemCsmReady${c.i}`].value}));
  out.nearRadius = gi.uniforms.lemNearRadius.value;
  out.shadowFit = gi._shadowFit && {r: gi._shadowFit.radius,
    c: gi._shadowFit.centre.toArray().map(v => +v.toFixed(1))};
  out.mapSize = gi.sun?.shadow?.mapSize?.width;
  out.ao = gi.uniforms.lemAOStrength.value;
  return out;
});
console.log(JSON.stringify(info, null, 1));

async function shoot(name, patch) {
  await page.evaluate(patch);
  await page.waitForTimeout(500);
  await page.screenshot({path: path.join(OUT, name + '.png')});
}

/* Each variant is re-applied every frame, because the module's own service
 * routines rewrite these uniforms as the cascades refit. */
await page.evaluate(() => {
  const gi = window.__gi;
  window.__mode = 'base';
  const orig = gi.update.bind(gi);
  gi.update = (dt, t) => {
    orig(dt, t);
    const u = gi.uniforms, m = window.__mode;
    if (m === 'noao') { u.lemAOStrength.value = 0; u.lemAOContact.value = 0; }
    if (m === 'nocsm') { u.lemCsmReady0.value = 0; u.lemCsmReady1.value = 0; }
    if (m === 'nocsm0') { u.lemCsmReady0.value = 0; }
    if (m === 'nocsm1') { u.lemCsmReady1.value = 0; }
    if (m === 'nonear') { gi.sun.castShadow = false; }
    if (m === 'onlynear') { u.lemCsmReady0.value = 0; u.lemCsmReady1.value = 0;
                            u.lemAOStrength.value = 0; u.lemAOContact.value = 0; }
    if (m === 'nogi') { u.lemGIStrength.value = 0; }
  };
});

for (const mode of ['base', 'noao', 'nocsm', 'nocsm0', 'nocsm1', 'nonear', 'onlynear']) {
  await shoot(mode, `window.__mode = ${JSON.stringify(mode)};` +
    (mode === 'nonear' ? '' : 'window.__gi.sun.castShadow = true;'));
}

if (errors.length) console.log('CONSOLE:', JSON.stringify([...new Set(errors)].slice(0, 12), null, 1));
await browser.close();
console.log('wrote', OUT);
