/* shadowcost.mjs — exact cost of three's shadow pass, by wrapping it.
 *   node shadowcost.mjs URL */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);

console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), e = w.engine, r = e.renderer;
  const sm = r.shadowMap;
  const stats = [];
  const orig = sm.render.bind(sm);
  sm.render = (lights, scene, camera) => {
    const c0 = r.info.render.calls, t0 = r.info.render.triangles;
    orig(lights, scene, camera);
    stats.push({calls: r.info.render.calls - c0, tris: r.info.render.triangles - t0});
  };
  const farStats = [];
  const origFar = gi._renderCascade.bind(gi);
  gi._renderCascade = (c) => {
    const c0 = r.info.render.calls, t0 = r.info.render.triangles;
    origFar(c);
    farStats.push({i: c.i, calls: r.info.render.calls - c0,
                   tris: r.info.render.triangles - t0, radius: c.radius});
  };
  const frames = [];
  for (let i = 0; i < 90; i++) {
    e.shadowNeedsUpdate = true;
    await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
    frames.push({calls: e.drawCalls, tris: e.triangles});
  }
  const avg = a => a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0;
  const hot = stats.filter(s => s.calls > 0);
  return {
    shadowPass: {n: hot.length, calls: Math.round(avg(hot.map(s => s.calls))),
                 tris: Math.round(avg(hot.map(s => s.tris)))},
    farPass: [0, 1].map(i => {
      const f = farStats.filter(s => s.i === i && s.calls > 0);
      return {i, n: f.length, calls: Math.round(avg(f.map(s => s.calls))),
              tris: Math.round(avg(f.map(s => s.tris))), radius: f[0]?.radius};
    }),
    frameCalls: Math.round(avg(frames.map(f => f.calls))),
    frameTris: Math.round(avg(frames.map(f => f.tris))),
    maxCalls: Math.max(...frames.map(f => f.calls)),
    maxTris: Math.max(...frames.map(f => f.tris)),
  };
}), null, 1));
await b.close();
