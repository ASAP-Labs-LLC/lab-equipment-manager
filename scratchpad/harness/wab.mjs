/* wab.mjs — load the world, run an arbitrary patch expression against it, wait,
 * screenshot. For deciding whether a water knob matters before editing the file.
 *
 *   node wab.mjs <url> <out.png> "<js body, `w` is the world, `T` is terrain>"
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const [,, url, out, patch = ''] = process.argv;
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--enable-unsafe-swiftshader','--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,400)));
p.on('console', m => { if (m.type()==='error' && !/favicon|404/.test(m.text())) console.log('ERR', m.text().slice(0,300)); });
await p.goto(url + (url.includes('?')?'&':'?') + 'hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(2500);
if (patch) {
  const r = await p.evaluate(src => {
    const w = window.__lemWorld, T = w.subsystems.get('terrain');
    try { return String(new Function('w','T',src)(w,T)); } catch (e) { return 'PATCHERR '+e; }
  }, patch);
  console.log('patch ->', r);
}
await p.waitForTimeout(2500);
fs.mkdirSync(out.replace(/\/[^/]*$/,''), {recursive:true});
await p.screenshot({path: out});
const st = await p.evaluate(() => window.__lemWorld.stats());
console.log(JSON.stringify(st));
await b.close();
