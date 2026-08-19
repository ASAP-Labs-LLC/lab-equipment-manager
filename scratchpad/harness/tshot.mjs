/* tshot.mjs — screenshot with an optional in-page mutation, so "which
 * subsystem is painting that" is measured rather than argued about.
 *
 *   node tshot.mjs --url "..." --out a.png [--eval "js"] [--seconds 4]
 *
 * `--eval` runs after the world is ready and before the shot; it is a plain
 * statement list evaluated with `w` bound to window.__lemWorld.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const next = process.argv[i + 1];
  args[a.slice(2)] = (!next || next.startsWith('--')) ? true : (i++, next);
}
let url = args.url;
if (!/[?&]hud=/.test(url)) url += (url.includes('?') ? '&' : '?') + 'hud=0';
const out = path.resolve(args.out);
fs.mkdirSync(path.dirname(out), {recursive: true});

const b = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'],
});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
const errors = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300)); });
p.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)));

await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(Math.max(1500, (parseFloat(args.seconds || '4')) * 1000));

let probe = null;
if (args.eval) {
  probe = await p.evaluate(src => {
    const w = window.__lemWorld;
    try { return (0, eval)('(function(w){' + src + '})')(w) ?? null; }
    catch (e) { return 'EVAL ERROR ' + String(e); }
  }, args.eval);
  await p.waitForTimeout(900);
}
const stats = await p.evaluate(() => window.__lemWorld.stats());
await p.screenshot({path: out});
console.log(JSON.stringify({out, stats, probe, errors}, null, 1));
await b.close();
