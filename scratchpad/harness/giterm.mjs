/* giterm.mjs — strip one shading term at a time from the terrain and shoot the
 * same frame, so the caster-less dark region can be attributed to albedo,
 * indirect, direct or shadow rather than argued about. */
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
const OUT = path.resolve(args.out || '../shots/giterm');
fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);

const shot = async name => {
  await page.waitForTimeout(1200);
  await page.screenshot({path: path.join(OUT, name + '.png')});
  console.log('  ' + name);
};

await shot('0-base');

/* Terrain albedo out: white base colour, no map. If the dark region survives
 * this it is not painted on. */
const info = await page.evaluate(() => {
  const w = window.__lemWorld;
  window.__saved = [];
  const out = [];
  w.scene.traverse(o => {
    if (!o.isMesh && !o.isInstancedMesh) return;
    if (!/terrain/i.test(o.name || '')) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m) continue;
      window.__saved.push({m, map: m.map, color: m.color?.clone(),
                           normalMap: m.normalMap, rough: m.roughnessMap,
                           ao: m.aoMap, defines: m.defines && {...m.defines}});
      out.push({name: o.name, mat: m.name || m.type, map: !!m.map,
                nmap: !!m.normalMap, rmap: !!m.roughnessMap, aomap: !!m.aoMap,
                onBefore: !!m.onBeforeCompile});
      m.map = null; m.normalMap = null; m.roughnessMap = null; m.aoMap = null;
      m.color?.setRGB(0.5, 0.5, 0.5);
      m.needsUpdate = true;
    }
  });
  return out;
});
console.log(JSON.stringify(info, null, 1));
await shot('1-terrain-white');
await page.evaluate(() => {
  for (const s of window.__saved) {
    s.m.map = s.map; s.m.normalMap = s.normalMap; s.m.roughnessMap = s.rough;
    s.m.aoMap = s.ao;
    if (s.color) s.m.color.copy(s.color);
    s.m.needsUpdate = true;
  }
});

/* Vertex colours out, if the terrain paints with them. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  window.__vc = [];
  w.scene.traverse(o => {
    if (!/terrain/i.test(o.name || '')) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || !m.vertexColors) continue;
      window.__vc.push(m); m.vertexColors = false; m.needsUpdate = true;
    }
  });
  return window.__vc.length;
});
await shot('2-no-vcol');
await page.evaluate(() => {
  for (const m of window.__vc || []) { m.vertexColors = true; m.needsUpdate = true; }
});

/* Sun off entirely: what is left is indirect alone. */
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.__i = gi.sun.intensity; gi.sun.intensity = 0;
});
await shot('3-no-sun');
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.intensity = gi.__i;
});

/* Indirect off: what is left is direct alone. */
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.__g = gi.uniforms.lemGIStrength.value;
  gi.uniforms.lemGIStrength.value = 0;
  gi.__e = window.__lemWorld.scene.environmentIntensity;
  window.__lemWorld.scene.environmentIntensity = 0;
});
await shot('4-no-indirect');
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.uniforms.lemGIStrength.value = gi.__g;
  window.__lemWorld.scene.environmentIntensity = gi.__e;
});

/* Fog / aerial perspective out, in case the dark region is atmosphere. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  window.__fog = w.scene.fog;
  w.scene.fog = null;
  w.scene.traverse(o => {
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) if (m && m.fog) { m.fog = false; m.needsUpdate = true; }
  });
});
await shot('5-no-fog');

await browser.close();
