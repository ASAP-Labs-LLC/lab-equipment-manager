import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1400,height:900}, deviceScaleFactor:2})).newPage();
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000});
await p.waitForTimeout(14000);
await p.click('#btnQuality'); await p.waitForTimeout(500);
for (const m of ['auto','full','max']) {
  await p.click(`#resList input[value=${m}]`); await p.waitForTimeout(1100);
  console.log(m.padEnd(5), '->', await p.evaluate(()=>document.getElementById('resNow').textContent));
}
await b.close();
