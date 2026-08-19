/* vsync pins the frame at 8.3ms on this machine, so the only way to price the
 * dome is to render it many times back to back with a finish in between. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:false, channel:'chromium'});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, e = w.engine, r = e.renderer, gl = r.getContext();
  const sky = w.ctx.sky;
  const run = (steps) => {
    sky._uniforms.uCloudSteps.value = steps;
    const scene = e.scene, cam = e.camera;
    r.setRenderTarget(e._targets.scene);
    for (let i = 0; i < 5; i++) r.render(scene, cam);   // warm
    const px = new Uint8Array(4);
    const sync = () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    sync();
    const t0 = performance.now();
    const N = 60;
    for (let i = 0; i < N; i++) r.render(scene, cam);
    sync();
    const ms = (performance.now() - t0) / N;
    r.setRenderTarget(null);
    return ms;
  };
  const s14 = run(14), s0 = run(0), s14b = run(14), s4 = run(4);
  sky._uniforms.uCloudSteps.value = 14;
  return {msWithClouds14: +s14.toFixed(3), msWithClouds14b: +s14b.toFixed(3),
          msNoClouds: +s0.toFixed(3), msClouds4: +s4.toFixed(3)};
})));
await b.close();
