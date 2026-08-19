/* tterm.mjs — three's own near-shadow term, with the trains as the only casters.
 *
 * tproj.mjs showed, on the CPU, that a ground point beside a tank car projects
 * onto a texel holding the tank car, with four metres of depth margin. So the
 * map is right and the projection is right. This paints `getShadow()` straight
 * out of the receiving materials with nothing else in the world casting, which
 * separates "the lookup returns lit" from "the lookup returns dark and the
 * shading throws it away".
 */
import {chromium} from 'playwright';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/tterm.png');
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)); });
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);

console.log(JSON.stringify(await p.evaluate(async (only) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  w.engine.updaters = [];
  if (only) {
    const set = new Set();
    tr.root.traverse(o => set.add(o));
    w.scene.traverse(o => {
      if ((o.isMesh || o.isInstancedMesh) && !set.has(o)) o.castShadow = false;
    });
    for (const c of gi._csm || []) { c.casters.length = 0; c.dirty = true; }
    if (tr.contactMesh) tr.contactMesh.visible = false;
  }
  const body = `
    float dbgS = getShadow( directionalShadowMap[0], directionalLightShadows[0].shadowMapSize,
                            1.0, directionalLightShadows[0].shadowBias,
                            directionalLightShadows[0].shadowRadius, vDirectionalShadowCoord[0] );
    outgoingLight = vec3( dbgS );`;
  const seen = new Set();
  let patched = 0;
  w.scene.traverse(o => {
    if (!o.isMesh && !o.isInstancedMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || !m.isMeshStandardMaterial || seen.has(m.uuid)) continue;
      seen.add(m.uuid);
      const prev = m.onBeforeCompile;
      m.onBeforeCompile = (sh, r) => {
        prev?.call(m, sh, r);
        sh.fragmentShader = sh.fragmentShader.replace(
          '#include <opaque_fragment>',
          `\n#ifdef USE_SHADOWMAP\n${body}\n#endif\n#include <opaque_fragment>`);
      };
      m.customProgramCacheKey = () => 'tterm';
      m.needsUpdate = true;
      patched++;
    }
  });
  w.engine.shadowNeedsUpdate = true;
  await new Promise(r => setTimeout(r, 1200));
  return {patched};
}, args.all ? false : true)));
await p.waitForTimeout(1200);
await p.screenshot({path: OUT});
await b.close();
console.log('wrote', OUT);
