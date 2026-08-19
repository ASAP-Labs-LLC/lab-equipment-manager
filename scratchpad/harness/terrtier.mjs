import {chromium} from 'playwright';
const url = process.argv[2], out = process.argv[3];
const browser = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport:{width:1920,height:1080}});
const errs=[]; page.on('console',m=>{if(m.type()==='error')errs.push(m.text().slice(0,200));});
await page.goto(url+'&hud=0',{waitUntil:'load',timeout:60000});
await page.waitForFunction(()=>window.__lemWorld?.subsystems?.size>0,null,{timeout:60000});
await page.waitForTimeout(2500);
for (const t of ['low','floor']) {
  await page.evaluate(n => {
    const w = window.__lemWorld;
    w.subsystems.get('terrain').onQuality({name:n, shadow:1024});
  }, t);
  await page.waitForTimeout(2000);
  await page.locator('#world').screenshot({path: out.replace('.png', '-'+t+'.png')});
}
console.log(JSON.stringify({errs}));
await browser.close();
