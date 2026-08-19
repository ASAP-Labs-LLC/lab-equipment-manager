/* vfinal.mjs — the before/after, with each of this round's three changes taken
 * out one at a time on the same frame. The painting change needs its own page
 * load (it happens at build); the two shader ones are uniforms and come out
 * live, so three of the four states share one session and cannot differ by
 * anything else. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const BASE = process.argv[2];
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/FIN';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const errs = [];

async function session(url, tag) {
  const p = await b.newPage({viewport:{width:1920,height:1080}});
  p.on('pageerror', e => errs.push(tag + ': ' + String(e).slice(0,160)));
  await p.goto(url + '&quality=ultra', {waitUntil:'load', timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
  await p.waitForTimeout(10000);
  const shot = async n => { await p.waitForTimeout(1400);
    fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };
  const off = () => p.evaluate(() => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    for (const m of v.materials) { const L = m.userData?.lem; if (!L) continue;
      L.uVegSharp?.value.set(99, 100);
      if (L.uVegCover) L.uVegCover.value = [1,1,1,1,1,1,1,1]; }
  });
  const stats = await p.evaluate(() => {
    const w = window.__lemWorld, r = w.engine.renderer;
    return {draws: r.info.render.calls, tris: r.info.render.triangles,
            build: w.subsystems.get('vegetation')._buildMs | 0};
  });
  return {p, shot, off, stats};
}

const a = await session(BASE, 'new');
console.log('new stats', JSON.stringify(a.stats));
await a.shot('new');
await a.off();
await a.shot('new-shaderoff');
await a.p.close();

const c = await session(BASE + '&vegnoclose=1', 'old');
console.log('old stats', JSON.stringify(c.stats));
await c.shot('paintoff');
await c.off();
await c.shot('original');
await c.p.close();

console.log('errors', JSON.stringify(errs.slice(0,6)));
await b.close();
