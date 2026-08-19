import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=yard&time=15&hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.evaluate(() => { for (let i=0;i<6;i++) window.__lemWorld.parse(['multitek-ns','optimpp-1','pac-flash-1'][i%3],'L-1'); });
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  return T.consists.filter(c=>c.group&&c.group.visible).slice(0,6).map(c => ({
    slot:c.slot, state:c.state, s:+(c.s||0).toFixed(1), v:+(c.v||0).toFixed(2),
    groupPos:[+c.group.position.x.toFixed(1),+c.group.position.y.toFixed(1),+c.group.position.z.toFixed(1)],
    firstVehicleWorld: (() => {
      const v = c.vehicles && c.vehicles[0];
      if (!v || !v.group) return null;
      const w = new (window.__lemWorld.constructor === Object ? Object : Object)();void w;
      const p3 = v.group.getWorldPosition(new (c.group.position.constructor)());
      return [+p3.x.toFixed(1), +p3.y.toFixed(1), +p3.z.toFixed(1)];
    })(),
    hasRoute: !!c.route, line: c.line,
  }));
}), null, 1));
await b.close();
