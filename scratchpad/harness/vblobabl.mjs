/* vblobabl.mjs — is the dark round mass on the spit vegetation at all?
 *
 *   node vblobabl.mjs --out DIR
 *
 * One page session, four captures: everything, then each vegetation tier hidden
 * in turn. Same frame, same exposure history, same layout — so a pixel that
 * changes is that tier and nothing else. Written because a raycast through the
 * same pixels answered "terrain" and a raycast can miss an alpha card, and
 * because this project's standing rule is that an ablation which reports zero
 * is suspected before it is believed.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const dir = arg('out', '/tmp/vblobabl');
fs.mkdirSync(dir, {recursive: true});
const W = 1920, H = 1080;

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?cam=far&time=9&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: W, height: H}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(10000);

/* Pin the stop in BOTH halves, at the SAME number, per REQUESTS.md — a lock
 * alone leaves each half at whatever it had adapted to. */
await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  if (gi) { gi._expNow = 3.0; if (gi.setExposureLocked) gi.setExposureLocked(true); }
});
await p.waitForTimeout(1500);

const shot = async (tag) => {
  await p.waitForTimeout(1200);
  await p.screenshot({path: `${dir}/${tag}.png`});
};

await shot('all');
for (const tier of ['tree', 'clutter', 'sward', 'grass', 'ALL']) {
  await p.evaluate((t) => {
    const veg = window.__lemWorld.subsystems.get('vegetation');
    const show = (m, v) => { if (m) m.visible = v; };
    /* restore everything first */
    for (const e of (veg.trees || [])) { show(e.near, true); show(e.far, true); show(e.trunk, true); }
    for (const c of (veg.clutter || [])) show(c.mesh, true);
    for (const s of (veg.sward || [])) show(s.mesh, true);
    if (veg.grass) show(veg.grass.mesh, true);
    veg.group.visible = true;
    if (t === 'ALL') { veg.group.visible = false; }
    else if (t === 'tree') for (const e of (veg.trees || [])) { show(e.near, false); show(e.far, false); show(e.trunk, false); }
    else if (t === 'clutter') for (const c of (veg.clutter || [])) show(c.mesh, false);
    else if (t === 'sward') for (const s of (veg.sward || [])) show(s.mesh, false);
    else if (t === 'grass') { if (veg.grass) show(veg.grass.mesh, false); }
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  }, tier);
  await shot('no-' + tier);
}
console.log('wrote', dir);
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
