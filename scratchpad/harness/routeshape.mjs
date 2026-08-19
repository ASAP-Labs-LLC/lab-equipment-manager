import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail');
  const uid = w.plan.stations[0].uid;
  const r = rail.route ? rail.route(uid) : null;
  const out = {railMethods: Object.getOwnPropertyNames(Object.getPrototypeOf(rail)).slice(0,25)};
  if (r) {
    out.routeKeys = Object.keys(r).slice(0, 20);
    out.routeProto = Object.getOwnPropertyNames(Object.getPrototypeOf(r)).slice(0, 20);
    out.isCurve = !!r.isCurve;
    out.sample = typeof r.pointAt === 'function' ? 'pointAt' :
                 typeof r.getPointAt === 'function' ? 'getPointAt' :
                 typeof r.at === 'function' ? 'at' : null;
    for (const k of ['length','totalLength','len','points','pts','curve','path','spline'])
      if (r[k] !== undefined) out['has_'+k] = Array.isArray(r[k]) ? `array(${r[k].length})` : typeof r[k];
  } else out.route = null;
  return out;
}), null, 1));
await b.close();
