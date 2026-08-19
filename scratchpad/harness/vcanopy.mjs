/* vcanopy.mjs — dump the outer wood's clump page as it is actually painted,
 * over a checker so alpha is visible. Every previous round of this file that
 * guessed at what a painting looked like from the render was wrong about it. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const out = process.argv[2] || '../shots/vcanopy.png';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1100, height: 1100}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=vegetation&hud=0',
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
const png = await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.canopy.image;
  const c = document.createElement('canvas');
  c.width = src.width; c.height = src.height;
  const g = c.getContext('2d');
  for (let y = 0; y < c.height; y += 32) {
    for (let x = 0; x < c.width; x += 32) {
      g.fillStyle = ((x / 32 + y / 32) & 1) ? '#8ab4d8' : '#d8e4ee';
      g.fillRect(x, y, 32, 32);
    }
  }
  g.drawImage(src, 0, 0);
  return c.toDataURL('image/png').slice(22);
});
fs.writeFileSync(out, Buffer.from(png, 'base64'));
console.log('wrote', out);
await b.close();
