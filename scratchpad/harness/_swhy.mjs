/* Why is the sward being rejected? Re-walks the same lattice in-page, calling
 * the same predicates, and counts the causes separately. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,vegetation&cam=wide&time=16&hud=0&quality=ultra', {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3500);
const out = await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const isl = v.island, R = Math.max(isl.r, v.landR || 0) + 40;
  const CELL = 9.5, W = 15.5;
  const cells = Math.ceil(R / CELL);
  const c = {tot: 0, outside: 0, site: 0, open: 0, die: 0, ok: 0};
  const cov = [];
  let ground = 0, blocker = 0, rail = 0, hex = 0;
  for (let j = -cells; j <= cells; j++) for (let i = -cells; i <= cells; i++) {
    const x = isl.cx + i * CELL, z = isl.cz + j * CELL;
    if (Math.hypot(x - isl.cx, z - isl.cz) > R) continue;
    c.tot++;
    const h = v._ground(x, z);
    if (h - 0.05 < v.plantFloor) { c.site++; ground++; continue; }
    if (!v._clearOf(x, z, W * 0.42, 1.5, v.plantFloor, h)) {
      c.site++;
      let bl = false;
      for (const bk of v.blockers) { const dx = x - bk.x, dz = z - bk.z;
        if (dx * dx + dz * dz < (bk.r + W * 0.42) ** 2) { bl = true; break; } }
      if (bl) blocker++;
      else if (v._railCells && v._railDist(x, z, 9 + 1.5 + W * 0.42) < 9 + 1.5 + W * 0.42) rail++;
      else hex++;
      continue;
    }
    const open = v._openness(x, z, true);
    if (open < 0.06) { c.open++; continue; }
    const s = v._biome(x, z, h);
    const sh = v._shore(s);
    const shore = 1 - Math.min(1, sh.beach * 1.7 + sh.salt * 0.22);
    const cval = Math.min(1, (0.20 + 0.95 * open * open) *
      (1 - Math.min(0.75, s.slope * 0.55)) * (0.45 + 1.15 * s.wet) *
      (1 - s.rock * 0.7) * shore * (0.42 + 1.15 * 0.5));
    cov.push(cval);
    c.ok++;
  }
  cov.sort((a, b) => a - b);
  const q = f => +(cov[Math.floor(cov.length * f)] || 0).toFixed(2);
  return {...c, ground, blocker, rail, hex,
          plantFloor: +v.plantFloor.toFixed(1), waterLevel: +v.waterLevel.toFixed(1),
          coverP10: q(0.1), coverP50: q(0.5), coverP90: q(0.9),
          meanCover: +(cov.reduce((a, x) => a + x, 0) / cov.length).toFixed(2)};
});
await b.close();
console.log(JSON.stringify(out, null, 1));
