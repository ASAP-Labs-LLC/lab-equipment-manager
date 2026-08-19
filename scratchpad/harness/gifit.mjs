import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:1280,height:720}});
for (const cam of ['yard','wide','low','street','top']) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}&time=16&weather=clear&hud=0`, {waitUntil:'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
  await p.waitForTimeout(3500);
  console.log(cam, JSON.stringify(await p.evaluate(() => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi');
    return {dist: +(w.rig?.distance ?? -1).toFixed(1), nearReach: +gi._nearReach.toFixed(1),
            radius: gi._shadowFit.radius, texel_cm: +((gi._shadowFit.radius*2)/gi.sun.shadow.mapSize.x*100).toFixed(1),
            csm: gi._csm.map(c => ({r: +c.radius?.toFixed(0), texel_cm: +((c.radius*2)/c.rt.width*100).toFixed(1), casters: c.casters.length})),
            camY: +w.camera.position.y.toFixed(1)};
  })));
}
await b.close();
