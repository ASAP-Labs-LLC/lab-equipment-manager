/* pwshot.mjs — "permanent way" shot. Stands the camera a named distance off a
 * named track at a named arc length, with the target ON the rail head, so a
 * close-up of ballast/sleepers/rail lands on track rather than on whatever the
 * preset cameras happen to frame. Owned by the rail builder.
 *
 *   node pwshot.mjs --url "…" --out ../shots/x.png \
 *     --track load:0 --s 120 --side 6 --up 1.6 --dist 9 --yaw-off 0.4 --pitch 0.16
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
const out = path.resolve(args.out || 'pwshot.png');
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
await page.waitForTimeout(1200);

const cfg = {
  track: args.track || 'main', s: parseFloat(args.s ?? '100'),
  up: parseFloat(args.up ?? '1.5'), dist: parseFloat(args.dist ?? '9'),
  yawOff: parseFloat(args['yaw-off'] ?? '0.5'),
  pitch: parseFloat(args.pitch ?? '0.14'),
};
const info = await page.evaluate(c => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const tracks = rail?.tracks || [];
  const t = tracks.find(x => x.name === c.track) || tracks[0];
  if (!t?.frames) return {error: 'no track', names: tracks.map(x => x.name)};
  const f = t.at(Math.min(c.s, t.length - 1));
  const r = w.rig;
  r.idleDrift = false;
  r.goalTarget.set(f.position.x, f.position.y + c.up, f.position.z);
  r.target.copy(r.goalTarget);
  /* Look roughly along the track, offset by yawOff radians so the shot has the
   * line running away rather than straight at the lens. */
  const yaw = Math.atan2(f.tangent.x, f.tangent.z) + c.yawOff;
  r.goalYaw = yaw; r.goalPitch = c.pitch; r.goalDistance = c.dist;
  r.minDistance = 1.5;
  r.apply(1);
  return {track: t.name, len: Math.round(t.length),
          at: [f.position.x.toFixed(1), f.position.y.toFixed(1), f.position.z.toFixed(1)],
          names: tracks.map(x => x.name + ':' + Math.round(x.length))};
}, cfg);

await page.waitForTimeout(seconds * 1000);
const stats = await page.evaluate(() => window.__lemWorld.stats());
await page.screenshot({path: out});
fs.writeFileSync(out.replace(/\.png$/, '') + '.json',
                 JSON.stringify({url, out, cfg, ...stats, ...info, errors}, null, 2));
console.log(JSON.stringify({cfg, info, fps: stats.fps, drawCalls: stats.drawCalls,
                            triangles: stats.triangles, tier: stats.tier, errors}, null, 2));
await browser.close();
