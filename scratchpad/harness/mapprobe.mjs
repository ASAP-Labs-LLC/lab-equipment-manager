import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('console', m => { if (/SHDBG/.test(m.text())) console.log(m.text()); });
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, eng = w.engine, r = eng.renderer;
  const out = {};
  // force a redraw and count the draw calls it costs
  r.info.autoReset = false;
  r.info.reset();
  eng.shadowNeedsUpdate = true;
  await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
  out.callsShadowFrame = eng.drawCalls; out.trisShadowFrame = eng.triangles;
  await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
  out.callsPlainFrame = eng.drawCalls; out.trisPlainFrame = eng.triangles;

  let sun = null; w.scene.traverse(o => { if (o.isDirectionalLight) sun = o; });
  const map = sun.shadow.map;
  out.mapIsRT = !!map; out.mapW = map?.width; out.mapH = map?.height;
  out.texType = map?.texture?.type; out.texFormat = map?.texture?.format;
  out.hasDepthTexture = !!map?.depthTexture;
  // read a coarse grid of the packed-depth colour target
  const W = map.width, H = map.height;
  const buf = new Uint8Array(4 * 64 * 64);
  try {
    r.readRenderTargetPixels(map, (W>>1) - 32, (H>>1) - 32, 64, 64, buf);
    let distinct = new Set(), min = 1e9, max = -1e9;
    for (let i = 0; i < 64*64; i++) {
      const d = (buf[i*4]/255) + (buf[i*4+1]/255)/255 + (buf[i*4+2]/255)/65025 + (buf[i*4+3]/255)/16581375;
      distinct.add(buf[i*4]); if (d < min) min = d; if (d > max) max = d;
    }
    out.centreDepthMin = +min.toFixed(5); out.centreDepthMax = +max.toFixed(5);
    out.distinctTopBytes = distinct.size;
    out.sampleBytes = Array.from(buf.slice(0, 16));
  } catch (e) { out.readErr = String(e); }
  // full-map histogram at low res
  try {
    const b2 = new Uint8Array(4 * W * 1);
    r.readRenderTargetPixels(map, 0, H>>1, W, 1, b2);
    let lt = 0, ones = 0;
    for (let i = 0; i < W; i++) { const v = b2[i*4]; if (v < 250) lt++; if (v >= 254) ones++; }
    out.scanlineNonFar = lt; out.scanlineFar = ones;
  } catch (e) { out.readErr2 = String(e); }
  out.sunPos = sun.position.toArray().map(v=>+v.toFixed(1));
  out.sunTarget = sun.target.position.toArray().map(v=>+v.toFixed(1));
  out.shadowCamPos = sun.shadow.camera.position.toArray().map(v=>+v.toFixed(1));
  return out;
}), null, 1));
await b.close();
