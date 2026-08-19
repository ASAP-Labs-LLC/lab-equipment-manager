import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(6000);
const sample = (n) => p.evaluate(async (n) => {
  const e = window.__lemWorld.engine, c = [], t = [];
  for (let i = 0; i < n; i++) { await new Promise(r => requestAnimationFrame(r)); c.push(e.drawCalls); t.push(e.triangles); }
  const med = a => [...a].sort((x,y)=>x-y)[a.length>>1];
  return {calls: med(c), tris: med(t), max: Math.max(...c), min: Math.min(...c)};
}, n);
await p.evaluate(() => { const r = window.__lemWorld.engine.renderer; r.shadowMap.autoUpdate = true; });
const withS = await sample(40);
await p.evaluate(() => { const r = window.__lemWorld.engine.renderer; r.shadowMap.enabled = false; r.shadowMap.autoUpdate = false; });
const noS = await sample(40);
console.log(JSON.stringify({withShadowEveryFrame: withS, shadowPassDisabled: noS,
  shadowPassCalls: withS.calls - noS.calls, shadowPassTris: withS.tris - noS.tris}));
await b.close();
