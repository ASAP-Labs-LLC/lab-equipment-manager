/* vshot.mjs — screenshot with an arbitrary JS tweak applied first.
 *   node vshot.mjs <url> <out.png> "<js run in page>" */
import {chromium} from 'playwright';
const [url, out, js = ''] = process.argv.slice(2);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
const errs = [];
p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await p.waitForTimeout(2500);
if (js) await p.evaluate(js);
await p.waitForTimeout(1800);
await p.screenshot({path: out});
console.log(out, errs.length ? errs : 'ok');
await b.close();
