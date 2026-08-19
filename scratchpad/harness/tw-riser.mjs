/* tw-riser.mjs — what does `_splat` SEE on a bench batter?
 *
 * The batter is grass in the shipped splat (harness/tw-w.mjs: benchFace is
 * grass 0.45 + dryGrass 0.41, dirt 0.067, stone 0.070). This walks the two
 * risers at 1 m and prints every field the rule would key off, so the
 * thresholds for a worked-face rule are chosen against measured values rather
 * than against a drawing.
 *
 * `slope` is 1 - normal.Y, the unit every threshold in `_splat` is written in.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 800, height: 450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);

const out = await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const T = t._terrace;
  const q = new Float32Array(4);
  const D = 1.8;                                  // the core's own half-step
  const walk = (x, zc, half) => {
    const rows = [];
    for (let z = zc - half; z <= zc + half; z += 1) {
      const h = t.heightAt(x, z);
      const nat = t._baseHeight(x, z);
      const gx = (t.heightAt(x + D, z) - t.heightAt(x - D, z)) / (2 * D);
      const gz = (t.heightAt(x, z + D) - t.heightAt(x, z - D)) / (2 * D);
      const len = Math.sqrt(gx * gx + gz * gz + 1);
      const lap = (t.heightAt(x - D, z) + t.heightAt(x + D, z)
                 + t.heightAt(x, z - D) + t.heightAt(x, z + D)) * 0.25 - h;
      t._distances(x, z, q);
      rows.push({z: +z.toFixed(0), h: +h.toFixed(2), nat: +nat.toFixed(2),
                 moved: +(h - nat).toFixed(2),
                 slope: +(1 - 1 / len).toFixed(4),
                 deg: +(Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI).toFixed(1),
                 lap: +lap.toFixed(3),
                 mask: +t._benchMask(x, z).toFixed(2),
                 dFoot: +Math.min(q[0], t._railDist(x, z)).toFixed(1),
                 dPad: +q[1].toFixed(1), dBal: +q[2].toFixed(1)});
    }
    return rows;
  };
  const res = {cx: t.cx, risers: []};
  for (const r of T.risers) {
    const zc = r.z0 + r.run * 0.5;
    res.risers.push({rise: +r.rise.toFixed(2), run: +r.run.toFixed(2), zc: +zc.toFixed(1),
                     walk: walk(t.cx, zc, 22)});
  }
  /* and how wide the riser is in x, i.e. how much of the frame it can occupy */
  const r0 = T.risers[0], zc0 = r0.z0 + r0.run * 0.5;
  const xs = [];
  for (let x = -120; x <= 460; x += 20)
    xs.push([x, +t._benchMask(x, zc0).toFixed(2),
             +((t.heightAt(x, zc0 + 5) - t.heightAt(x, zc0 - 5)) / 10).toFixed(3)]);
  res.acrossX = xs;
  return res;
});
console.log(JSON.stringify(out, null, 0));
await b.close();
