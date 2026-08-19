import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2] + '&quality=ultra';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation');
  const s=v.shared||{};
  const out={};
  for (const k of Object.keys(s)) { const val=s[k].value; out[k]= val&&val.toArray?val.toArray():val; }
  return {shared: out, weather: w.ctx?.weather || w.weather};
})));
fs.writeFileSync(process.argv[3], await p.screenshot());
await b.close();
