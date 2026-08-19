import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.evaluate(() => { for (let i=0;i<10;i++) setTimeout(()=>window.__lemWorld.parse(['multitek-ns','optimpp-1','pac-flash-1','optimpp-2'][i%4],'L'), i*150); });
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const live = T.consists.filter(c=>c&&c.group&&c.group.visible&&c.route&&c.state!=='idle');
  return live.map(c => {
    const r = c.route;
    const len = r.totalLength || r.length || (r.getLength && r.getLength());
    let pt = null;
    try { pt = r.getPointAt ? r.getPointAt(Math.min(1,Math.max(0,c.s/len))) : null; } catch(e){ pt = 'threw: '+e.message; }
    return {slot:c.slot, state:c.state, line:c.line, s:+c.s.toFixed(1),
            lenProp:{totalLength:r.totalLength, length:r.length, getLength: !!r.getLength},
            resolvedLen: len, hasGetPointAt: !!r.getPointAt,
            point: pt && pt.x!==undefined ? [+pt.x.toFixed(1),+pt.y.toFixed(1),+pt.z.toFixed(1)] : pt};
  });
}), null, 1));
await b.close();
