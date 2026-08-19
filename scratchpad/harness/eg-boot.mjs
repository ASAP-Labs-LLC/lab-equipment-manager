/* Where does time-to-first-frame actually go? Times each subsystem's build by
 * loading module subsets: the marginal cost of a module is the difference it
 * makes to __worldReady, measured on the same page with everything else held.
 * Three repeats because a cold module graph parse is not free either. */
import {chromium} from 'playwright';
const BASE = 'sky,gi,terrain';
const ADD  = ['buildings','rail','trains','vegetation','weather'];
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
async function ready(mods) {
  const t=[];
  for (let i=0;i<3;i++){
    const p = await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
    const t0=Date.now();
    await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=far&time=9&weather=clear&hud=0&quality=ultra`,
                 {waitUntil:'load',timeout:60000});
    await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
    t.push(Date.now()-t0);
    await p.context().close();
  }
  t.sort((a,b)=>a-b); return t[1];
}
const base = await ready(BASE);
console.log(`base (${BASE}): ${base} ms`);
let prev = base, mods = BASE;
for (const m of ADD) {
  mods += ',' + m;
  const r = await ready(mods);
  console.log(`  +${m.padEnd(11)} ${r} ms   marginal +${r-prev} ms`);
  prev = r;
}
console.log(`full stack: ${prev} ms  (budget 3000)`);
await b.close();
