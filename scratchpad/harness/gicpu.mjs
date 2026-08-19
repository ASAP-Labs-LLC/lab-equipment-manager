/* gicpu.mjs — replicate lemFarShadow on the CPU for a grid of visible surface
 * points, so we can see exactly which test is rejecting. */
import {chromium} from 'playwright';

const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);

const out = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE;
  const rn = w.engine.renderer;
  const rc = new THREE.Raycaster();
  rc.layers.set(0);
  const targets = [];
  w.scene.traverse(o => { if (o.isMesh || o.isInstancedMesh) targets.push(o); });

  const maps = gi._csm.map(c => {
    const buf = new Uint8Array(c.rt.width * c.rt.height * 4);
    rn.readRenderTargetPixels(c.rt, 0, 0, c.rt.width, c.rt.height, buf);
    return {buf, size: c.rt.width};
  });
  const unpack = (m, u, v) => {
    const x = Math.min(m.size - 1, Math.max(0, Math.floor(u * m.size)));
    const y = Math.min(m.size - 1, Math.max(0, Math.floor(v * m.size)));
    const i = (y * m.size + x) * 4;
    return m.buf[i] / 255 + m.buf[i + 1] / 65025 + m.buf[i + 2] / 16581375;
  };

  const box0 = gi.uniforms.lemCsmBox0.value;
  const nearC = gi.uniforms.lemNearCentre.value;
  const nearR = gi.uniforms.lemNearRadius.value;
  const rx = gi.uniforms.lemLightRight.value, ru = gi.uniforms.lemLightUp.value;
  const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
  const boxW = (p, c, r) => {
    const dx = p.x - c.x, dy = p.y - c.y, dz = p.z - c.z;
    const q = Math.max(Math.abs(dx * rx.x + dy * rx.y + dz * rx.z),
                       Math.abs(dx * ru.x + dy * ru.y + dz * ru.z));
    return 1 - ss(r * 0.80, r * 0.97, q);
  };

  const rows = [];
  const stat = {samples: 0, nearFull: 0, out0: 0, out1: 0, lit0: 0, shadow0: 0,
                lit1: 0, shadow1: 0, noHit: 0};
  const v = new THREE.Vector2();
  const tmp = new THREE.Vector4();
  for (let gy = 1; gy <= 9; gy++) {
    for (let gx = 1; gx <= 15; gx++) {
      v.set(gx / 16 * 2 - 1, -(gy / 10 * 2 - 1));
      rc.setFromCamera(v, w.camera);
      const hits = rc.intersectObjects(targets, false);
      const hit = hits.find(h => {
        const m = Array.isArray(h.object.material) ? h.object.material[0] : h.object.material;
        return h.distance > 6 && m && !m.transparent && m.isMeshStandardMaterial &&
               (h.object.geometry?.boundingSphere?.radius || 0) < 3000;
      });
      if (!hit) { stat.noHit++; continue; }
      const p = hit.point;
      hits.length = 0; hits.push(hit);
      stat.samples++;
      const nw = boxW(p, nearC, nearR);
      if (rows.length < 30) {
        const dx = p.x - nearC.x, dy = p.y - nearC.y, dz = p.z - nearC.z;
        rows.push({sx: gx, sy: gy, hit: hits[0].object.name || hits[0].object.type,
                   dist: +hits[0].distance.toFixed(1),
                   p: [p.x, p.y, p.z].map(n => +n.toFixed(1)),
                   q: +Math.max(Math.abs(dx * rx.x + dy * rx.y + dz * rx.z),
                                Math.abs(dx * ru.x + dy * ru.y + dz * ru.z)).toFixed(1),
                   nearW: +nw.toFixed(3)});
      }
      if (nw >= 0.999) { stat.nearFull++; continue; }
      for (let i = 0; i < gi._csm.length; i++) {
        const m = gi.uniforms[`lemCsmMat${i}`].value;
        const par = gi.uniforms[`lemCsmParam${i}`].value;
        tmp.set(p.x, p.y, p.z, 1).applyMatrix4(m);
        const px = tmp.x / tmp.w, py = tmp.y / tmp.w, pz = tmp.z / tmp.w;
        const inside = px >= 0 && px <= 1 && py >= 0 && py <= 1 && pz >= 0 && pz <= 1;
        if (!inside) { stat['out' + i]++; continue; }
        const d = pz - par.z;
        const md = unpack(maps[i], px, py);
        if (d <= md) stat['lit' + i]++; else stat['shadow' + i]++;
        if (rows.length < 24) {
          rows.push({i, sx: gx, sy: gy, dist: +w.camera.position.distanceTo(p).toFixed(0),
                     p: [p.x, p.y, p.z].map(n => +n.toFixed(1)),
                     uv: [+px.toFixed(3), +py.toFixed(3)], pz: +pz.toFixed(4),
                     mapDepth: +md.toFixed(4), bias: +par.z.toFixed(5),
                     shadowed: d > md, w0: +boxW(p, box0, box0.w).toFixed(2),
                     nearW: +nw.toFixed(2)});
        }
      }
    }
  }
  return {stat, rows, box0: box0.toArray(), nearC: nearC.toArray(), nearR};
});
console.log(JSON.stringify(out, null, 1));
await browser.close();
