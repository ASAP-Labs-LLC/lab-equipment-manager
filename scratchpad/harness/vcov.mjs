import {chromium} from 'playwright';
let URL = process.argv[2];
if (!/[?&]quality=/.test(URL)) URL += '&quality=ultra';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,200)));
p.on('console',m=>{ if(/vegetation/.test(m.text())) console.log('LOG', m.text().slice(0,200)); });
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  const f=a=>a?Array.from(a).map(n=>+n.toFixed(2)):null;
  return {leaf:f(v.covLeaf), crown:f(v.covCrown), grove:f(v.covGrove),
          buildMs: v._buildMs|0,
          far: v.matFar?.userData.lem.uVegCover.value.map(n=>+n.toFixed(2)),
          grovemat: v.matGrove?.userData.lem.uVegCover.value.map(n=>+n.toFixed(2))};
})));
console.log('errors', JSON.stringify(errs));
await b.close();
