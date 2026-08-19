import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, e = w.engine, r = w.renderer || e.renderer;
  const rt = e._targets?.ao;
  const out = {have: !!rt, uniforms: {}};
  const u = e._passes?.ao?.material?.uniforms || {};
  for (const k of Object.keys(u)) {
    const v = u[k].value;
    if (typeof v === 'number') out.uniforms[k] = v;
    else if (v && v.isVector2) out.uniforms[k] = [v.x, v.y];
  }
  if (!rt) return out;
  const W = rt.width, H = rt.height;
  const buf = new Uint8Array(W * H * 4);
  try { r.readRenderTargetPixels(rt, 0, 0, W, H, buf); } catch (err) { out.err = String(err); return out; }
  const hist = new Array(10).fill(0);
  let n = 0, sum = 0, min = 255;
  for (let i = 0; i < W * H; i++) {
    const v = buf[i * 4];
    hist[Math.min(9, (v / 25.6) | 0)]++;
    sum += v; n++; if (v < min) min = v;
  }
  out.size = [W, H];
  out.mean = +(sum / n).toFixed(1);
  out.min = min;
  out.histPercent = hist.map(c => +(c / n * 100).toFixed(1));
  return out;
}), null, 1));
await b.close();
