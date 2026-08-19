/* bx-probe.mjs — what the sun does at time=9/16, and what the design plane
 * says at each bench against what the bench schedule asks for. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(3) : v;
  const sunAt = (tt) => {
    const sky = w.subsystems.get('sky');
    if (sky && sky.setTime) sky.setTime(tt);
    else if (w.ctx.emit) w.ctx.emit('time', {hour: tt});
    const s = w.ctx.sun || (sky && sky.sun) || null;
    const d = s && (s.position || s.dir);
    if (!d) return null;
    const L = Math.hypot(d.x, d.y, d.z) || 1;
    const x = d.x / L, y = d.y / L, z = d.z / L;
    // azimuth measured from +x (east) toward +z
    return {x: r2(x), y: r2(y), z: r2(z),
            elevDeg: r2(Math.asin(y) * 180 / Math.PI),
            azFromEastDeg: r2(Math.atan2(z, x) * 180 / Math.PI)};
  };
  const sb = w.ctx.siteBenches;
  const SITE_Y = 3.0;
  const rows = (sb.benches || []).map(bb => ({
    id: bb.id, cx: r2(bb.cx), cz: r2(bb.cz),
    minX: r2(bb.minX), maxX: r2(bb.maxX), minZ: r2(bb.minZ), maxZ: r2(bb.maxZ),
    level: r2(bb.level),
    planeAtCentre: r2(t._designAt(bb.cx, bb.cz)),
    wantY: r2(SITE_Y + bb.level),
    deltaAtCentre: r2(SITE_Y + bb.level - t._designAt(bb.cx, bb.cz)),
    planeAtMinX: r2(t._designAt(bb.minX, bb.cz)),
    planeAtMaxX: r2(t._designAt(bb.maxX, bb.cz)),
  }));
  const legs = (t.features || []).filter(f => f.t === 1 && f.kind === 'rail')
    .map(f => ({ax: r2(f.ax), az: r2(f.az), bx: r2(f.bx), bz: r2(f.bz), r: f.r}));
  return {
    sun9: sunAt(9), sun16: sunAt(16), sun13: sunAt(13),
    design: {a: r2(t.design.a), bx: r2(t.design.bx), bz: r2(t.design.bz)},
    yShift: r2(t.yShift), cx: r2(t.cx), cz: r2(t.cz), islandR: r2(t.islandR),
    coreSize: t.coreSize, waterY: r2(t.waterY),
    rows, legs,
    roads: (t.roads || []).map(r => r.map(v => r2(v))),
  };
});
out.pageErrors = errs;
console.log(JSON.stringify(out, null, 1));
await b.close();
