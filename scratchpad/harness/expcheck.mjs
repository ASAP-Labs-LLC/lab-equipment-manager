import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:640,height:360}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const u = w.engine._passes.composite.material.uniforms;
  return {before: u.uExposure.value, keys: Object.keys(u), tier: w.engine.tier.name};
})));
await p.evaluate(() => { window.__lemWorld.engine._passes.composite.material.uniforms.uExposure.value = 0.4; });
await p.waitForTimeout(600);
console.log(JSON.stringify(await p.evaluate(() => ({after: window.__lemWorld.engine._passes.composite.material.uniforms.uExposure.value}))));
await p.screenshot({path:'/tmp/expcheck.png'});
await b.close();
