/* vdepth.mjs — for a screen rectangle, which vegetation instances land in it
 * and how far away are they; and what per-channel fog factor sky.js's own
 * formula produces at those depths. Answers "is the haze landing on the far
 * tier too hard" with numbers instead of an opinion. */
import {chromium} from 'playwright';
const URL = process.argv[2];
const RECT = (process.argv[3] || '60,120,460,200').split(',').map(Number);
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(9000);
const out = await p.evaluate(([x0,y0,w,h]) => {
  const wo = window.__lemWorld, v = wo.subsystems.get('vegetation');
  const cam = wo.camera, W = 1920, H = 1080;
  const buckets = {near: [], far: [], grove: []};
  for (const mesh of v.meshes) {
    const kind = mesh.material === v.matNear ? 'near'
               : mesh.material === v.matFar ? 'far'
               : mesh.material === v.matGrove ? 'grove' : null;
    if (!kind || !mesh.visible) continue;
    const arr = mesh.instanceMatrix.array;
    for (let i = 0; i < mesh.count; i++) {
      const pos = cam.position.clone().set(
        arr[i * 16 + 12], arr[i * 16 + 13], arr[i * 16 + 14]);
      mesh.localToWorld(pos);
      const d = pos.distanceTo(cam.position);
      const s = pos.clone().project(cam);
      const sx = (s.x * 0.5 + 0.5) * W, sy = (1 - (s.y * 0.5 + 0.5)) * H;
      if (s.z > 1 || sx < x0 || sx > x0 + w || sy < y0 - 40 || sy > y0 + h + 40) continue;
      buckets[kind].push({d: Math.round(d), y: Math.round(pos.y)});
    }
  }
  const f = wo.scene.fog;
  const FOG_H = 130, FOG_MAX = 0.88, K = [0.80, 1.00, 1.42];
  const factor = (depth, hy) => {
    const h0 = cam.position.y, h1 = hy;
    const A = Math.exp(-Math.max(h0, -600) / FOG_H);
    const B = Math.exp(-Math.max(h1, -600) / FOG_H);
    const dy = h1 - h0;
    const avg = Math.abs(dy) < 1 ? 0.5 * (A + B) : FOG_H * (A - B) / dy;
    const tau = f.density * depth * Math.min(Math.max(avg, 0), 6);
    return K.map(k => FOG_MAX * (1 - Math.exp(-((tau * k) * (tau * k)))));
  };
  const summary = {};
  for (const k of Object.keys(buckets)) {
    const arr = buckets[k].sort((a, b) => a.d - b.d);
    if (!arr.length) { summary[k] = {n: 0}; continue; }
    const q = t => arr[Math.min(arr.length - 1, Math.floor(arr.length * t))];
    const med = q(0.5);
    summary[k] = {n: arr.length, p10: q(0.1).d, p50: med.d, p90: q(0.9).d,
                  medY: med.y,
                  fogAtMedian: factor(med.d, med.y).map(n => +n.toFixed(3))};
  }
  return {cam: cam.position.toArray().map(n => Math.round(n)),
          density: f.density, fogColorLinear: f.color.toArray(),
          rect: [x0, y0, w, h], summary,
          ladder: [200, 400, 600, 900, 1400, 2200, 3000].map(
            d => [d, factor(d, 40).map(n => +n.toFixed(3))])};
}, RECT);
console.log(JSON.stringify(out, null, 1));
await b.close();
