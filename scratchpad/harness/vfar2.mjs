/* vfar2.mjs — same ablation, but this time each change is READ BACK and the
 * fog is nailed shut against sky.js rewriting its density every frame. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const URL = process.argv[2];
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/F2';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0,200)));
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(9000);

const shot = async n => { await p.waitForTimeout(1400);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };

const say = async (label, fn) => console.log(label, JSON.stringify(await p.evaluate(fn)));

await say('grove-mat', () => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const m = v.matGrove;
  if (!m) return 'NO GROVE MATERIAL';
  const gi = window.__lemWorld.subsystems.get('gi');
  return {gi: !!gi?.materials?.has(m), env: m.envMapIntensity,
          base: m.userData.lemEnvBase, defines: Object.keys(m.defines||{}),
          lem: Object.fromEntries(Object.entries(m.userData.lem).map(
            ([k,u]) => [k, u.value?.toArray ? u.value.toArray() : u.value])),
          groveMeshes: v.meshes.filter(x=>x.material===m).map(x=>x.count)};
});

await shot('base');

/* fog nailed to zero */
await say('fog-off', () => {
  const f = window.__lemWorld.scene.fog;
  window.__d = f.density;
  Object.defineProperty(f, 'density', {get: () => 0, set: () => {}, configurable: true});
  return f.density;
});
await shot('nofog');
await say('fog-on', () => {
  const f = window.__lemWorld.scene.fog;
  delete f.density; f.density = window.__d; return f.density;
});

/* alpha window shut on the far tiers */
await say('edge-off', () => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const out = [];
  for (const m of [v.matFar, v.matGrove]) if (m) {
    m.userData.lem.uVegEdge.value = 0.0;
    m.userData.lem.uVegDither.value = 0.0;
    out.push([m.userData.lem.uVegEdge.value, m.userData.lem.uVegDither.value]); }
  return out;
});
await shot('hardcut');
await say('hardcut+nofog', () => {
  const f = window.__lemWorld.scene.fog;
  Object.defineProperty(f, 'density', {get: () => 0, set: () => {}, configurable: true});
  return f.density;
});
await shot('hardcut-nofog');
await say('fog-on2', () => { const f = window.__lemWorld.scene.fog;
  delete f.density; f.density = window.__d; return f.density; });
await say('edge-back', () => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  v.matFar.userData.lem.uVegEdge.value = 3.0;
  v.matFar.userData.lem.uVegDither.value = 0.26;
  if (v.matGrove) { v.matGrove.userData.lem.uVegEdge.value = 2.4;
                    v.matGrove.userData.lem.uVegDither.value = 0.0; }
  return 'ok';
});

/* a sanity check that uniform writes reach the frame at all */
await say('gain5', () => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) m.userData.lem.uVegGain.value = 4.0;
  return 'ok';
});
await shot('gain4');
await say('gain-back', () => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  v.matFar.userData.lem.uVegGain.value = 1.14;
  if (v.matGrove) v.matGrove.userData.lem.uVegGain.value = 1.04;
  return 'ok';
});

console.log('errors', JSON.stringify(errs));
await b.close();
