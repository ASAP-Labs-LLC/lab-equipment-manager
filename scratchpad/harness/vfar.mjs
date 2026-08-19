/* vfar.mjs — which mechanism makes the far tier blue-white and speckled?
 * Ablate one contributor at a time on ONE page session, same camera, same
 * frame, and shoot each. Crop + grade offline. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const URL = process.argv[2];
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/FAR';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0,200)));
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(9000);

const info = await p.evaluate(() => {
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const dump = m => m ? ({
    type: m.type, uuid: m.uuid.slice(0,6),
    alphaTest: m.alphaTest, transparent: m.transparent,
    envMapIntensity: m.envMapIntensity, envMap: !!m.envMap,
    lemEnvBase: m.userData?.lemEnvBase, noGI: !!m.userData?.noGI,
    roughness: m.roughness, metalness: m.metalness,
    fog: m.fog, side: m.side, defines: Object.keys(m.defines||{}),
    lem: Object.fromEntries(Object.entries(m.userData?.lem||{}).map(
      ([k,u]) => [k, u.value && u.value.toArray ? u.value.toArray() : u.value])),
  }) : null;
  const gi = w.subsystems.get('gi');
  const adopted = m => !!(gi && gi.materials && gi.materials.has(m));
  const meshes = (v.meshes||[]).map(m => ({
    name: m.name || '', count: m.count, visible: m.visible,
    mat: m.material.uuid.slice(0,6), tris: m.geometry.index
      ? m.geometry.index.count/3*m.count : 0}));
  return {
    cam: w.camera.position.toArray().map(n=>Math.round(n)),
    fog: w.scene.fog ? {density: w.scene.fog.density,
                        color: w.scene.fog.color.toArray()} : null,
    near: dump(v.matNear), giNear: adopted(v.matNear),
    far: dump(v.matFar), giFar: adopted(v.matFar),
    grove: dump(v.matGrove), giGrove: adopted(v.matGrove),
    meshes,
    treeSets: (v.trees||[]).map(e => ({near: e.near?.count, far: e.far?.count,
                                       trunk: e.trunk?.count})),
    groveMeshes: (v.groveMeshes||v.groves2||[]).length,
  };
});
console.log(JSON.stringify(info, null, 1));

const shot = async n => { await p.waitForTimeout(1400);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); };
const ev = (fn, arg) => p.evaluate(fn, arg);

await shot('base');

/* 1. fog off */
await ev(() => { window.__lemFogD = window.__lemWorld.scene.fog.density;
                 window.__lemWorld.scene.fog.density = 0.0; });
await shot('nofog');
await ev(() => { window.__lemWorld.scene.fog.density = window.__lemFogD; });

/* 2. the alpha edge window shut on the far tiers only */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) {
    m.userData.lem.uVegEdge.__old = m.userData.lem.uVegEdge.value;
    m.userData.lem.uVegEdge.value = 0.0; } });
await shot('noedge');

/* 3. …and the mip dither too, i.e. a plain hard cutout on the far tiers */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) {
    m.userData.lem.uVegDither.__old = m.userData.lem.uVegDither.value;
    m.userData.lem.uVegDither.value = 0.0; } });
await shot('hardcut');

/* 4. hard cutout AND no fog — is anything left blue? */
await ev(() => { window.__lemWorld.scene.fog.density = 0.0; });
await shot('hardcut-nofog');
await ev(() => { window.__lemWorld.scene.fog.density = window.__lemFogD; });

/* restore */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) {
    m.userData.lem.uVegEdge.value = m.userData.lem.uVegEdge.__old;
    m.userData.lem.uVegDither.value = m.userData.lem.uVegDither.__old; } });

/* 5. groves hidden — what is far card vs grove? */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.meshes) if (m.material === v.matGrove) m.visible = false; });
await shot('nogrove');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.meshes) if (m.material === v.matGrove) m.visible = true;
  for (const e of v.trees) if (e.far) e.far.visible = false; });
await shot('nofarcard');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees) if (e.far) e.far.visible = true; });

/* 6. vegetation gone entirely — what colour is the hillside behind it? */
await ev(() => { window.__lemWorld.subsystems.get('vegetation').group.visible = false; });
await shot('noveg');

console.log('errors', JSON.stringify(errs));
await b.close();
