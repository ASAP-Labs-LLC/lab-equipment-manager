/* vuni.mjs — poke vegetation's own uniforms on a live page and shoot each
 * setting, so a tint can be chosen against a measurement of the frame instead
 * of against an argument about what colour a leaf is.
 *
 *   node vuni.mjs <url> <tag> '[{"name":"a","set":{"matNear.uVegWrapTint":[..]}}]'
 * Dotted keys are <materialField>.<uniform>; "*" as the material means every
 * foliage material. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3];
const sets = JSON.parse(process.argv[4]);
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = [];
p.on('console', m => { const t = m.text();
  if (m.type()==='error' && !/404/.test(t)) errs.push(t); });
p.on('pageerror', e => errs.push(String(e)));
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(9000);
if (process.argv[5]) await p.evaluate(c => { window.__vcam = c; },
                                      JSON.parse(process.argv[5]));
/* Own the camera — see vjudge.mjs. solo.html's `at=` loses a race with the
 * fleet fetch often enough that two runs of the same sweep frame different
 * trees, which is fatal to a sweep. */
await p.evaluate(() => {
  const w = window.__lemWorld;
  const c = (window.__vcam || [1.95, 0.06, 62]);
  const b = w.plan && w.plan.bounds;
  const s = c[3] === 'centre' && b
    ? {x: (b.minX + b.maxX) / 2, z: (b.minZ + b.maxZ) / 2}
    : (w.plan && w.plan.byUid.get(c[3] || 'multitek-ns'));
  Object.assign(w.rig, {goalYaw: c[0], goalPitch: c[1], goalDistance: c[2]});
  w.rig.goalTarget.set(s ? s.x : 0, 4, s ? s.z : 0);
  w.rig.apply(1); w.rig.idleDrift = false;
});
await p.waitForTimeout(2500);
for (const s of sets) {
  const missed = await p.evaluate(({set}) => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    const miss = [];
    for (const [key, val] of Object.entries(set)) {
      const [mk, uk] = key.split('.');
      const mats = mk === '*' ? ['matNear','matFar','matClutter','matGrass'] : [mk];
      for (const name of mats) {
        const u = v[name] && v[name].userData.lem && v[name].userData.lem[uk];
        if (!u) { miss.push(name + '.' + uk); continue; }
        if (Array.isArray(val)) u.value.set(val[0], val[1], val[2]);
        else u.value = val;
      }
    }
    return miss;
  }, {set: s.set});
  if (missed.length) console.log('MISSING', missed.join(','));
  await p.waitForTimeout(1600);
  fs.writeFileSync(dir + tag + '-' + s.name + '.png', await p.screenshot());
  console.log(tag + '-' + s.name);
}
if (errs.length) console.log('ERRORS', errs.slice(0,6));
await b.close();
