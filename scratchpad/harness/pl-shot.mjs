/* pl-shot.mjs — photograph the crossover, framed on the crossover.
 * Points the rig at the link's own midpoint rather than at a station, and
 * reports whether any world module changed while the shutter was open. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const OUT = arg('out', '/tmp/pl-shot.png');
const DIST = parseFloat(arg('distance', '95'));
const PITCH = parseFloat(arg('pitch', '0.42'));
const YAW = parseFloat(arg('yaw', '2.2'));
const MODS = arg('mods', 'terrain,rail,buildings,trains,vegetation,sky,gi');

const stamp = async () => {
  const names = ['rail', 'terrain', 'trains', 'buildings', 'vegetation', 'sky',
                 'gi', 'engine', 'index', 'labels', 'weather', 'camera'];
  const out = [];
  for (const n of names) {
    try {
      const r = await fetch(`http://127.0.0.1:5601/static/world/${n}.js`,
                            {method: 'HEAD'});
      out.push(n + ':' + (r.headers.get('last-modified') || '?'));
    } catch { out.push(n + ':err'); }
  }
  return out.join('|');
};

const before = await stamp();
const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1400, height: 800}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=yard&time=15&hud=0&quality=ultra`, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(4000);
const info = await p.evaluate(([d, pi, ya]) => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const link = rail.tracks.find(t => t.name.startsWith('link:'));
  if (!link) return {err: 'no link built'};
  const q = link.at(link.length / 2).position;
  Object.assign(w.rig, {goalYaw: ya, goalPitch: pi, goalDistance: d});
  w.rig.goalTarget.set(q.x, q.y + 1, q.z);
  w.rig.apply(1);
  w.rig.idleDrift = false;
  const road = rail.tracks.find(t => t.name === 'load:0');
  return {link: link.name, at: [+q.x.toFixed(1), +q.y.toFixed(1), +q.z.toFixed(1)],
          paved: (road.paved || []).map(s => s.map(v => +v.toFixed(1))),
          linkBlocks: link.blocks.map(s => s.map(v => +v.toFixed(1))),
          roadBlocks: road.blocks.map(s => s.map(v => +v.toFixed(1)))
                          .sort((a, c) => a[0] - c[0]),
          linkRuling: +(link.ruling || 0).toFixed(4),
          linkOverGrade: link.overGrade};
}, [DIST, PITCH, YAW]);
await p.waitForTimeout(2500);
await p.screenshot({path: OUT});
await b.close();
const after = await stamp();
console.log(JSON.stringify({...info, buildStable: before === after,
                            pageErrors: errs}, null, 1));
if (before !== after) console.error('UNSTABLE BUILD — a world module changed during capture');
