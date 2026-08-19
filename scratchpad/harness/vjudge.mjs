/* vjudge.mjs — a screenshot from a camera this file owns.
 *
 * `solo.html?at=<uid>` aims the rig at a station only if the fleet has already
 * arrived from /api/machines when the page's setup runs, and it often has not:
 * the same command five minutes apart framed two different parts of the site,
 * which makes every before-and-after in a tuning loop worthless. This sets the
 * target, yaw, pitch and distance itself, after the world is up, so two runs of
 * it are the same frame whatever else in the scene is being edited.
 *
 *   node vjudge.mjs <url> <out.png> <yaw> <pitch> <dist> [targetUid] [seconds]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const [url, out, yaw, pitch, dist, uid = 'multitek-ns', secs = '9'] =
  process.argv.slice(2);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('console', m => { const t = m.text();
  if (m.type() === 'error' && !/404/.test(t)) errs.push(t); });
p.on('pageerror', e => errs.push(String(e)));
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await p.waitForTimeout(Number(secs) * 1000);
const where = await p.evaluate(({yaw, pitch, dist, uid}) => {
  const w = window.__lemWorld;
  const s = w.plan && w.plan.byUid.get(uid);
  const t = s ? [s.x, 4, s.z] : [0, 4, 0];
  Object.assign(w.rig, {goalYaw: +yaw, goalPitch: +pitch, goalDistance: +dist});
  w.rig.goalTarget.set(t[0], t[1], t[2]);
  w.rig.apply(1);
  w.rig.idleDrift = false;
  return {found: !!s, t};
}, {yaw, pitch, dist, uid});
await p.waitForTimeout(2500);
fs.writeFileSync(out, await p.screenshot());
const stats = await p.evaluate(() => window.__lemWorld.stats());
console.log(JSON.stringify({...where, ...stats, errs: errs.slice(0, 4)}));
await b.close();
