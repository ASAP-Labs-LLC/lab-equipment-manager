import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  if (!gi) return {no: 'gi'};
  const keys = Object.keys(gi).filter(k => /casc|shadow|split|far/i.test(k));
  const out = {keys};
  for (const k of keys) {
    const v = gi[k];
    if (Array.isArray(v)) out[k] = v.length + ' entries: ' + JSON.stringify(v.map(x =>
      x && x.camera ? {span: Math.round((x.camera.right - x.camera.left)), far: Math.round(x.camera.far)} :
      (typeof x === 'object' && x ? Object.keys(x).slice(0,6) : x)).slice(0,4));
    else if (typeof v !== 'object' || v === null) out[k] = v;
    else out[k] = Object.keys(v).slice(0, 8);
  }
  return out;
}), null, 1));
await b.close();
