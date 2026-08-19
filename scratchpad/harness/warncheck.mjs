import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
for (const q of [0, 2, 4]) {
  const p = await b.newPage({viewport:{width:960,height:540}});
  const msgs = [];
  p.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') msgs.push(m.type()+': '+m.text().slice(0,180)); });
  p.on('pageerror', e => msgs.push('pageerror: ' + String(e).slice(0,180)));
  await p.goto(process.argv[2], {waitUntil:'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
  await p.waitForTimeout(2000);
  await p.evaluate(t => window.__lemWorld.engine.setTier(t, {force:true}), q);
  await p.waitForTimeout(2500);
  // also cycle weather + time so every recompile path is hit
  await p.evaluate(() => { const w = window.__lemWorld; w.setTime?.(20); w.setWeather?.('storm'); });
  await p.waitForTimeout(2000);
  const tier = await p.evaluate(() => window.__lemWorld.stats().tier);
  console.log(`tier ${tier}: ` + (msgs.filter(m => !/favicon|404/.test(m)).join(' | ') || 'clean'));
  await p.close();
}
await b.close();
