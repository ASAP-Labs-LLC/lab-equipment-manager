/* giblob.mjs — hold a moving consist still, then take the same frame with one
 * lighting contributor removed at a time.
 *
 *   node giblob.mjs --out ../shots/giblob --cam yard --time 16
 *
 * The soft dark region under a working has been blamed on four different things
 * across five rounds. A film shows it exists; only an A/B on a frozen frame can
 * say which term paints it, because every one of them moves with the train.
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
const OUT = path.resolve(args.out || '../shots/giblob');
fs.mkdirSync(OUT, {recursive: true});
const MODS = args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=${args.weather || 'clear'}&hud=0`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__blobParse = setInterval(() => w.parse(uids[i++ % uids.length], 'L-BLOB'), 1100);
});
await page.waitForTimeout(parseInt(args.settle || '9000', 10));

/* Freeze everything that moves, so every variant below is the same pixel grid
 * with one term changed. */
const frozen = await page.evaluate(() => {
  clearInterval(window.__blobParse);
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  const state = (T?.consists || []).filter(c => c && c.state !== 'idle')
    .map(c => ({slot: c.slot, state: c.state, s: +(c.s || 0).toFixed(1)}));
  if (T) T.update = () => {};
  const W = w.subsystems.get('weather'); if (W) W.update = () => {};
  const V = w.subsystems.get('vegetation'); if (V) V.update = () => {};
  /* gi's near cull rewrites `castShadow` on everything within the box every
   * quarter second, so any experiment that clears the flag is undone before the
   * screenshot. It has to be stopped first or every A/B below reads as null. */
  const gi = w.subsystems.get('gi');
  if (gi) { gi._nearCull = () => {}; gi._demoteCaster = () => false; }
  return state;
});
console.log('frozen consists:', JSON.stringify(frozen));

const shot = async name => {
  await page.waitForTimeout(900);
  await page.screenshot({path: path.join(OUT, name + '.png')});
  console.log('  ', name);
};

await shot('00-base');

const V = {
  /* three's own near cascade */
  'nonear': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.sun.castShadow = false;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  },
  'nonear-off': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.sun.castShadow = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  },
  /* the two coarse cascades */
  'nofar': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.__r0 = gi.uniforms.lemCsmReady0.value;
    gi.__r1 = gi.uniforms.lemCsmReady1.value;
    gi.uniforms.lemCsmReady0.value = 0;
    gi.uniforms.lemCsmReady1.value = 0;
    gi._serviceCascades = () => {};
  },
  'nofar-off': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.uniforms.lemCsmReady0.value = gi.__r0;
    gi.uniforms.lemCsmReady1.value = gi.__r1;
  },
  /* screen-space AO, both where it lands */
  'noao': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.__ao = gi.uniforms.lemAOStrength.value;
    gi.__ac = gi.uniforms.lemAOContact.value;
    gi.uniforms.lemAOStrength.value = 0;
    gi.uniforms.lemAOContact.value = 0;
  },
  'noao-off': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.uniforms.lemAOStrength.value = gi.__ao;
    gi.uniforms.lemAOContact.value = gi.__ac;
  },
  /* the probe field: swap it for the open-field hemisphere everywhere */
  'flatgi': () => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi.__pr = gi.uniforms.lemProbeR.value;
    gi.uniforms.lemProbeR.value = null;
    gi.uniforms.lemProbeG.value = null;
    gi.uniforms.lemProbeB.value = null;
  },
  'flatgi-off': () => {},
  /* Hide the working consists themselves. The difference against the base frame
   * is exactly two things — the vehicles' own pixels and the pixels their shadow
   * owns — so it is the only way to see the train's shadow separately from
   * whatever the terrain is already doing underneath it. */
  'notrain': () => {
    const T = window.__lemWorld.subsystems.get('trains');
    window.__hid = [];
    for (const c of (T?.consists || [])) {
      if (!c || c.state === 'idle') continue;
      const g = c.group || c.root || c.object3D || c.obj;
      if (g) { window.__hid.push(g); g.visible = false; }
    }
    window.__lemWorld.engine.shadowNeedsUpdate = true;
    return window.__hid.length;
  },
  'notrain-off': () => {
    for (const g of (window.__hid || [])) g.visible = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  },
};

/* Everything in the scene stops casting, and the light keeps its map. If the
 * dark region survives an empty shadow map it is not a cast shadow at all. */
if (args.nocastall) {
  const r = await page.evaluate(() => {
    const w = window.__lemWorld;
    const gi = w.subsystems.get('gi');
    let n = 0, kinds = {};
    w.scene.traverse(o => {
      if (!o.castShadow || o === gi.sun || o.isLight) return;
      o.castShadow = false; n++;
      kinds[o.type] = (kinds[o.type] || 0) + 1;
    });
    const r = w.renderer || w.engine.renderer;
    w.engine.shadowNeedsUpdate = true;
    if (r) r.shadowMap.needsUpdate = true;
    return {n, kinds, sunCasts: gi.sun.castShadow,
            mapSize: gi.sun.shadow?.map ? [gi.sun.shadow.map.width, gi.sun.shadow.map.height] : null};
  });
  console.log('   nocastall', r);
  await shot('40-nocastall');
}

/* Who casts the static dark region? Turn one subsystem's casting off at a time,
 * leaving it visible, so the diff is purely the shade it throws. */
const nocast = who => {
  const w = window.__lemWorld;
  window.__was = [];
  const roots = [];
  for (const [name, sub] of w.subsystems) {
    if (name !== who) continue;
    for (const k of ['root', 'group', 'meshes', 'sun']) {
      const v = sub[k];
      if (v && v.isObject3D) roots.push(v);
    }
    if (!roots.length) {
      w.scene.traverse(o => { if (o.userData?.lemSub === who) roots.push(o); });
    }
  }
  let n = 0;
  for (const r of roots) r.traverse(o => {
    if (o.castShadow) { window.__was.push(o); o.castShadow = false; n++; }
  });
  w.engine.shadowNeedsUpdate = true;
  return {roots: roots.length, off: n};
};
for (const who of (args.nocast ? args.nocast.split(',') : [])) {
  const r = await page.evaluate(nocast, who);
  console.log('   nocast', who, JSON.stringify(r));
  await shot('30-nocast-' + who);
  await page.evaluate(() => {
    for (const o of (window.__was || [])) o.castShadow = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  });
}

const only = args.only === true ? [] :
  args.only ? args.only.split(',') :
  args.nocastall || args.nocast ? [] : ['nonear', 'nofar', 'noao'];
for (const key of only) {
  await page.evaluate(V[key]);
  await shot('10-' + key);
  if (V[key + '-off']) await page.evaluate(V[key + '-off']);
}

/* And all of them at once: what is left is albedo × exposure. */
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.castShadow = false;
  gi.uniforms.lemCsmReady0.value = 0;
  gi.uniforms.lemCsmReady1.value = 0;
  gi._serviceCascades = () => {};
  gi.uniforms.lemAOStrength.value = 0;
  gi.uniforms.lemAOContact.value = 0;
  window.__lemWorld.engine.shadowNeedsUpdate = true;
});
await shot('20-noshadow-noao');

fs.writeFileSync(OUT + '-meta.json', JSON.stringify({url, frozen, errors}, null, 2));
await ctx.close();
await browser.close();
if (errors.length) console.log('ERRORS', errors.slice(0, 3));
console.log('out:', OUT);
