import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, e = w.engine, gi = w.subsystems.get('gi');
  gi._renderFar = () => {};                        // base scene only
  const c = [], t = [];
  for (let i = 0; i < 200; i++) { await new Promise(r => requestAnimationFrame(r)); c.push(e.drawCalls); t.push(e.triangles); }
  const med = a => [...a].sort((x,y)=>x-y)[a.length>>1];
  return {baseMed: med(c), baseMax: Math.max(...c), baseMedT: med(t), baseMaxT: Math.max(...t)};
})));
await b.close();
