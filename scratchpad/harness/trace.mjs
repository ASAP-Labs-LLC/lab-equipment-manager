import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage();
p.on('pageerror', e => console.log('[pageerror]', e.stack ? e.stack.split('\n').slice(0,4).join('\n') : String(e)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForTimeout(3000);
await b.close();
