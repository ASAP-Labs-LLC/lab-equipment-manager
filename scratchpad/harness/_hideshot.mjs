import {chromium} from 'playwright';
const hide = (process.argv[2]||'').split(',').filter(Boolean);
const out = process.argv[3];
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&cam=wide&time=16&quality=ultra', {waitUntil:'load', timeout:90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
await page.waitForTimeout(1500);
await page.evaluate((names) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  for (const m of t.meshes) if (names.includes(m.name.split('-').slice(1).join('-').split('-')[0]) || names.includes(m.name)) m.visible = false;
}, hide);
await page.waitForTimeout(900);
await page.screenshot({path: out});
await browser.close();
console.log('ok');
