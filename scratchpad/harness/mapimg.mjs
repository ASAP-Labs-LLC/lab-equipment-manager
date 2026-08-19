/* Dump the sun's shadow map (RGBA-packed depth) as a viewable PNG. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
const dataUrl = await p.evaluate(async () => {
  const w = window.__lemWorld, r = w.engine.renderer;
  w.engine.shadowNeedsUpdate = true;
  await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
  let sun = null; w.scene.traverse(o => { if (o.isDirectionalLight) sun = o; });
  const map = sun.shadow.map, W = map.width, H = map.height;
  const buf = new Uint8Array(4 * W * H);
  r.readRenderTargetPixels(map, 0, 0, W, H, buf);
  const N = 512, step = W / N;
  const cv = document.createElement('canvas'); cv.width = N; cv.height = N;
  const cx = cv.getContext('2d'); const img = cx.createImageData(N, N);
  let mn = 1, mx = 0;
  const d = new Float32Array(N*N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const sx = Math.floor(x*step), sy = Math.floor((N-1-y)*step);
    const i = (sy*W + sx)*4;
    const v = buf[i]/255 + buf[i+1]/65025 + buf[i+2]/16581375 + buf[i+3]/4228250625;
    d[y*N+x] = v; if (v < mn) mn = v; if (v > mx && v < 0.999) mx = v;
  }
  for (let i = 0; i < N*N; i++) {
    const t = Math.max(0, Math.min(1, (d[i]-mn)/Math.max(1e-6, mx-mn)));
    const c = d[i] > 0.999 ? 40 : Math.round(255*(1-t));
    img.data[i*4]=c; img.data[i*4+1]=c; img.data[i*4+2]=c; img.data[i*4+3]=255;
  }
  cx.putImageData(img, 0, 0);
  console.log('range', mn, mx);
  return {url: cv.toDataURL('image/png'), mn, mx};
});
console.log('depth range', dataUrl.mn, dataUrl.mx);
fs.writeFileSync(process.argv[3], Buffer.from(dataUrl.url.split(',')[1], 'base64'));
await b.close();
