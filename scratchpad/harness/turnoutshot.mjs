/* turnoutshot.mjs — put the camera on a junction and look at it.
 *
 *   node turnoutshot.mjs --out ../shots/turnout.png [--which 0] [--dist 34]
 *                        [--pitch 0.28] [--yaw 2.2] [--time 16]
 *
 * The turnouts are where the audit said the railway fell apart, so they are
 * what has to be looked at. Rather than guess a camera preset that happens to
 * frame one, this asks rail.js where its junctions actually are and stands the
 * camera at the switch tip.
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
const out = path.resolve(args.out || '../shots/turnout.png');
const which = parseInt(args.which || '0', 10);
const dist = parseFloat(args.dist || '34');
const pitch = parseFloat(args.pitch || '0.28');
const yaw = parseFloat(args.yaw || '2.2');
const time = args.time || '16';
const mods = args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
fs.mkdirSync(path.dirname(out), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}` +
            `&cam=street&time=${time}&weather=${args.weather || 'clear'}&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text()))
  errs.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(2500);

const info = await page.evaluate(([which, dist, pitch, yaw]) => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail');
  const list = rail._turnouts || [];
  if (!list.length) return {error: 'no turnouts'};
  const rec = list[Math.min(which, list.length - 1)];
  const f = rec.track.at(rec.s);
  /* Stand a little way along the diverging road so the blades, the frog and
   * the closure rails are all in frame rather than edge-on. */
  const c = rec.child.at(rec.which === 'start' ? 14 : rec.child.length - 14);
  w.rig.goalTarget.set((f.position.x + c.position.x) / 2,
                       (f.position.y + c.position.y) / 2 + 1.2,
                       (f.position.z + c.position.z) / 2);
  w.rig.goalDistance = dist;
  w.rig.goalPitch = pitch;
  w.rig.goalYaw = yaw;
  w.rig.apply(1);
  w.rig.idleDrift = false;
  const rep = rail.jointReport();
  return {count: list.length,
          at: `${rec.track.name}@${rec.s.toFixed(1)}→${rec.child.name}`,
          N: rec.N, hand: rec.hand, pdir: rec.pdir, which: rec.which,
          worstGapMm: rep.worstGapMm, worstAngle: rep.worstAngle};
}, [which, dist, pitch, yaw]);
await page.waitForTimeout(2500);
await page.screenshot({path: out});
const stats = await page.evaluate(() => window.__lemWorld.stats());
console.log(JSON.stringify({...info, ...stats, errors: errs}, null, 1));
await browser.close();
