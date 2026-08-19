import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const w = window.__lemWorld;
  const out = {stations: w.plan.stations.length};
  const uid = w.plan.stations[0].uid;
  const cyc = rail.cycle ? rail.cycle(uid) : null;
  if (cyc) {
    out.cycleKeys = Object.keys(cyc).slice(0,14);
    if (Array.isArray(cyc.segments))
      out.segments = cyc.segments.slice(0,12).map(s => ({
        track: s.track || s.name || '?', from: s.a ?? s.from, to: s.b ?? s.to,
        dir: s.dir}));
    out.closed = cyc.closed;
    out.length = cyc.length || cyc.totalLength;
  }
  const r = rail.route ? rail.route(uid) : null;
  if (r) { out.routeLen = r.totalLength || r.length; out.routeClosed = r.closed; }
  return out;
}), null, 1));
await b.close();
