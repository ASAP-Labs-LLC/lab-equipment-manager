import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1600,height:900}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(3500);
await p.screenshot({path: process.argv[3]});
// Ask for exactly one shadow-map redraw, then settle and shoot again.
await p.evaluate(() => { window.__lemWorld.engine.shadowNeedsUpdate = true; });
await p.waitForTimeout(1200);
await p.screenshot({path: process.argv[4]});
await b.close();
