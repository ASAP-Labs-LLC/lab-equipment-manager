/* sh-perf.mjs — the budget line: draw calls, triangles, first-frame time, and
 * the compiled fog chunk's own length, with and without the shell, so a shader
 * change is not allowed to cost the frame silently.
 *
 *   node sh-perf.mjs [--fog '{"t0":0,"p":3.25,"s":1}'] [--cam far]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather'
  + `&cam=${a.cam || 'far'}&time=${a.time || '9'}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
if (a.fog) await page.addInitScript(`window.__lemFog = ${a.fog};`);
const t0 = Date.now();
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
const ready = Date.now() - t0;
await page.waitForTimeout(12000);
const out = await page.evaluate(async () => {
  const w = window.__lemWorld;
  const s = w.stats ? w.stats() : {};
  const T = await import('three');
  const src = T.ShaderChunk.fog_fragment || '';
  /* frame time over 90 rAFs, after everything has settled */
  const ts = [];
  await new Promise(r => { let n = 0, last = performance.now();
    const step = () => { const t = performance.now(); ts.push(t - last); last = t;
      if (++n < 90) requestAnimationFrame(step); else r(); };
    requestAnimationFrame(step); });
  ts.sort((x, y) => x - y);
  return {stats: s, chunkChars: src.length,
          frameMs: {p50: +ts[45].toFixed(2), p95: +ts[85].toFixed(2)}};
});
console.log(JSON.stringify({readyMs: ready, ...out, errs: errs.slice(0, 3)}, null, 1));
await b.close();
