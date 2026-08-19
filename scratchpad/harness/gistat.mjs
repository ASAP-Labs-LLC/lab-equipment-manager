import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:800,height:450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=yard&time=16&weather=clear&hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(() => window.__lemWorld.plan.stations.map(s => ({uid:s.uid, x:Math.round(s.x), z:Math.round(s.z)})))));
await b.close();
