import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
const errs=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{if(m.type()==='error')errs.push(m.text().slice(0,200));});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
for (const t of [0,3,4]) {
  await p.evaluate(t => { const e = window.__lemWorld.engine; e.autoQuality = false; e.setTier(t, {force:true}); }, t);
  await p.waitForTimeout(2500);
  const info = await p.evaluate(() => ({tier: window.__lemWorld.engine.tier.name,
    dc: window.__lemWorld.engine.drawCalls, tri: window.__lemWorld.engine.triangles,
    steps: window.__lemWorld.ctx.sky._uniforms.uCloudSteps.value,
    detail: window.__lemWorld.ctx.sky._uniforms.uDetailOn.value}));
  await p.screenshot({path: `${process.argv[3]}-t${info.tier}.png`});
  console.log(JSON.stringify(info));
}
console.log('errors', JSON.stringify(errs));
await b.close();
