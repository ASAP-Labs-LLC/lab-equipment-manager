import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], out = process.argv[3];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url, {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(5000);
fs.mkdirSync(out, {recursive:true});
console.log(JSON.stringify(await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  return {ev: gi._sceneEV, evLow: gi._sceneEVLow, exp: gi.exposure, analytic: gi.analyticExposure};
})));
await p.screenshot({path: `${out}/metered.png`});
await p.evaluate(() => {                 // analytic only, black point as it was
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi._sceneEV = null; gi._sceneEVLow = undefined; gi._meter = () => {};
  gi._consumeMeter = () => {}; gi._expNow = gi.analyticExposure;
});
await p.waitForTimeout(2500);
await p.screenshot({path: `${out}/analytic.png`});
await b.close();
