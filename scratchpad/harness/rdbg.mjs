import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
p.on('console', m => { const t=m.text(); if (/rail|error|Error/i.test(t)) console.log('['+m.type()+']', t.slice(0,300)); });
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0,400)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail');
  const r = rail.earthworkReport ? rail.earthworkReport() : null;
  return {ringR: rail.ringR, waterY: rail.waterY, dead: rail.deadTracks,
          exceptions: rail.exceptions, structures: rail.structures,
          tracks: rail.tracks.map(t=>t.name), branches: rail.branches.length,
          sidings: [...rail.sidings.keys()].length,
          report: r};
}), null, 1));
await b.close();
