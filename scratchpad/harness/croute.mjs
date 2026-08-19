import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.evaluate(()=>{for(let i=0;i<8;i++) setTimeout(()=>window.__lemWorld.parse(['multitek-ns','optimpp-1','pac-flash-1','optimpp-2'][i%4],'L'), i*140);});
await p.waitForTimeout(8000);
console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const live = T.consists.filter(c=>c&&c.group&&c.group.visible&&c.route&&c.state!=='idle');
  if (!live.length) return {none:true, states: T.consists.slice(0,6).map(c=>c.state)};
  const c = live[0], r = c.route;
  return {
    n: live.length,
    ctorName: r && r.constructor ? r.constructor.name : null,
    ownKeys: r ? Object.keys(r).slice(0,20) : null,
    protoMethods: r ? Object.getOwnPropertyNames(Object.getPrototypeOf(r)).slice(0,20) : null,
    sampleTry: (() => {
      try { if (typeof r.pointAtDistance === 'function') { const q = r.pointAtDistance(c.s); return q ? [+q.x.toFixed(1),+q.y.toFixed(1),+q.z.toFixed(1)] : 'returned '+q; } return 'no pointAtDistance'; }
      catch(e){ return 'threw: '+e.message; }
    })(),
  };
}), null, 1));
await b.close();
