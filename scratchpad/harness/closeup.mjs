/* closeup.mjs — shot.mjs with the camera driven by hand.
 *
 * The solo harness's `street` preset stands 62m off, which is fine for reading
 * a line across a landscape and useless for judging a sleeper against
 * refs/tf2-03.jpg, where the camera is about four metres from the ballast.
 * This does the same capture and the same measurement, and additionally sets
 * the rig's target/yaw/pitch/distance from the command line.
 *
 *   node closeup.mjs --url "…" --out ../shots/x.png \
 *     --tx 0 --ty 1.6 --tz -34 --yaw 1.9 --pitch 0.10 --dist 9
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const next = process.argv[i + 1];
  if (!next || next.startsWith('--')) args[a.slice(2)] = true;
  else { args[a.slice(2)] = next; i++; }
}
const url = args.url;
if (!url) { console.error('need --url'); process.exit(2); }
const out = path.resolve(args.out || 'closeup.png');
const seconds = parseFloat(args.seconds || '3');
const width = parseInt(args.w || '1920', 10);
const height = parseInt(args.h || '1080', 10);
fs.mkdirSync(path.dirname(out), {recursive: true});

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=metal',
         '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width, height}, deviceScaleFactor: 1});
const errors = [];
const NOISE = /favicon\.ico|status of 404 \(NOT FOUND\)$/;
page.on('console', m => {
  if (m.type() === 'error' && !NOISE.test(m.text())) errors.push(m.text().slice(0, 400));
});
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 400)));

await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await page.waitForTimeout(1500);

const cam = {
  tx: parseFloat(args.tx ?? '0'), ty: parseFloat(args.ty ?? '1.5'),
  tz: parseFloat(args.tz ?? '0'), yaw: parseFloat(args.yaw ?? '0.9'),
  pitch: parseFloat(args.pitch ?? '0.12'), dist: parseFloat(args.dist ?? '10'),
};
const info = await page.evaluate(c => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  /* Snap the camera to a point on the railway if one was not named, so a
   * close-up never lands on empty grass because the layout moved. */
  const r = w.rig;
  r.idleDrift = false;
  r.goalTarget.set(c.tx, c.ty, c.tz);
  r.target.copy(r.goalTarget);
  r.goalYaw = c.yaw; r.goalPitch = c.pitch; r.goalDistance = c.dist;
  r.minDistance = 2;
  r.apply(1);
  const tracks = (rail?.tracks || []).map(t => ({
    name: t.name, len: Math.round(t.length),
    a: t.frames ? [Math.round(t.frames.pos[0]), Math.round(t.frames.pos[2])] : null,
    b: t.frames ? [Math.round(t.frames.pos[(t.frames.count - 1) * 3]),
                   Math.round(t.frames.pos[(t.frames.count - 1) * 3 + 2])] : null,
  }));
  const routes = [];
  for (const s of w.plan.stations) {
    const rt = rail?.route?.(s.uid);
    routes.push({uid: s.uid, len: rt ? Math.round(rt.length) : null,
                 head: rt ? [Math.round(rt.points[0].x), Math.round(rt.points[0].z)] : null});
  }
  return {tracks, routes, signals: rail?.signals?.length ?? 0};
}, cam);

await page.waitForTimeout(seconds * 1000);
const stats = await page.evaluate(() => window.__lemWorld.stats());
await page.screenshot({path: out});
fs.writeFileSync(out.replace(/\.png$/, '') + '.json',
                 JSON.stringify({url, out, cam, ...stats, ...info, errors}, null, 2));
console.log(JSON.stringify({cam, ...stats, errors, signals: info.signals}, null, 2));
console.log(JSON.stringify(info.tracks));
console.log(JSON.stringify(info.routes));
await browser.close();
