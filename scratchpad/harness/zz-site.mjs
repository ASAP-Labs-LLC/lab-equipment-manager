import {chromium} from 'playwright';
const layout = process.argv[2] || '';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra'
  + (layout ? '&layout=' + layout : '');
await p.goto(url,{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, T = w.subsystems.get('terrain'), R = w.subsystems.get('rail');
  const plan = w.plan;
  const H = (x,z)=> T?.heightAt ? T.heightAt(x,z) : 0;
  const out = {
    stations: plan.stations.map(s=>({uid:s.uid,x:+s.x.toFixed(1),z:+s.z.toFixed(1),g:+H(s.x,s.z).toFixed(2)})),
    hub: {x:plan.hub.x, z:plan.hub.z, g:+H(plan.hub.x,plan.hub.z).toFixed(2)},
    bounds: plan.bounds,
    islandR: T?.islandR, waterY: T?.waterY, ringSize: T?.ringSize,
    cx: T?.cx, cz: T?.cz, coastWobble: T?.coastWobble,
    tracks: (R?.tracks||[]).map(t=>({name:t.name, len:+(t.length||0).toFixed(0),
      minR:+(t.minRadiusUsed||0).toFixed(0), tight:!!t.tight,
      ruling:+(t.ruling||0).toFixed(3), meanFill:+(t.meanFill||0).toFixed(2),
      overGrade:+(t.overGrade||0).toFixed(3)})),
  };
  // a coarse height field over the site, 40m grid
  const b0 = plan.bounds;
  const x0 = Math.round(b0.minX-420), x1 = Math.round(b0.maxX+420);
  const z0 = Math.round(b0.minZ-420), z1 = Math.round(b0.maxZ+420);
  const rows = [];
  for (let z=z0; z<=z1; z+=40) {
    const r = [];
    for (let x=x0; x<=x1; x+=40) r.push(Math.round(H(x,z)));
    rows.push(r);
  }
  out.field = {x0,x1,z0,z1,step:40,rows};
  return out;
}), null, 1));
await b.close();
