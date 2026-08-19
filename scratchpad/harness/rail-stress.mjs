import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
const errs=[];
p.on('console', m=>{ if(m.type()==='error') errs.push(m.text().slice(0,300)); });
p.on('pageerror', e=>errs.push('pageerror: '+String(e).slice(0,300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail,trains&cam=wide&time=9');
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(1200);
const out = await p.evaluate(async () => {
  const w = window.__lemWorld, r = [];
  const rail = w.subsystems.get('rail');
  for (const t of ['floor','low','medium','high','ultra']) {
    const i = ['ultra','high','medium','low','floor'].indexOf(t);
    w.engine.setTier(i, {force:true});
    await new Promise(z=>setTimeout(z,120));
    r.push(t + ':' + w.stats().drawCalls + '/' + Math.round(w.stats().triangles/1000) + 'k');
  }
  /* a replan with a moved instrument, then a parse, then weather + night */
  const fleet = w.machines.map(m => ({...m}));
  fleet[0] = {...fleet[0], pos:[8.2, 2.05]};
  w.setMachines(fleet);
  await new Promise(z=>setTimeout(z,600));
  const routes = w.plan.stations.map(s => { const q = rail.route(s.uid); return q?Math.round(q.length):null; });
  w.parse(w.plan.stations[0].uid,'L-1');
  w.setWeather({preset:'rain', rain:0.9, wetness:0.9, fog:0.4});
  w.setTimeOfDay(21.5);
  await new Promise(z=>setTimeout(z,900));
  rail.occupy('t1', new (window.__lemWorld.engine.scene.constructor===Object?Object:Object)());
  rail.release('t1');
  return {tiers:r, routes, signals: rail.signals.length,
          tracks: rail.tracks.length, yard: !!rail.yardRoute()};
});
console.log(JSON.stringify(out));
console.log('ERRORS', JSON.stringify(errs));
await b.close();
