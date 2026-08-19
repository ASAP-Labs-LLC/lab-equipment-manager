/* islframe.mjs — is it an island FROM THE DEFAULT CAMERA?
 *
 * The acceptance criterion for the island round is a composition, not a radius:
 * sea past the land on more than one side, a coastline the eye can follow, the
 * mainland across open water behind it. Eyeballing a PNG cannot separate "sea"
 * from "pale haze on distant ground", and the whole point of the round is that
 * the two looked the same at 1347 m.
 *
 * So this measures the frame geometrically instead of by colour: it unprojects
 * a grid of pixels, marches each ray against `terrain.heightAt`, and classifies
 * the first thing it meets as land, sea, or nothing (sky / past the world). No
 * shading, no fog, no vegetation — a ray that ends in water ends in water even
 * if the pixel it belongs to is painted haze.
 *
 * Usage:
 *   node islframe.mjs --cam wide [--mods terrain] [--grid 128]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
}
const cam = a.cam || 'wide';
const mods = a.mods || 'terrain';
const grid = +(a.grid || 128);
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&hud=0`
          + `&quality=ultra&cam=${cam}&time=16&weather=clear`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errors = [];
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 240)); });
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 240)));
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await page.waitForTimeout(900);

const out = await page.evaluate(({G}) => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false;
  w.rig.apply(1);
  const cam = w.camera;
  cam.updateMatrixWorld(true);
  const t = w.subsystems.get('terrain');
  if (!t) return {error: 'no terrain'};
  const waterY = t.waterY;

  const origin = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  /* Rays built from fov and the camera's own basis rather than three's
   * unproject, because solo.html does not put THREE on window. */
  const e = cam.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]};
  const by = {x: e[4], y: e[5], z: e[6]};
  const bz = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360);
  const tx = ty * cam.aspect;

  /* March a ray until it drops below the surface, then bisect. FAR is past the
   * far plane on purpose: a ray that leaves the world is 'sky', and knowing
   * that is how "the mainland is behind the top edge" gets reported rather
   * than guessed. */
  const FAR = 9000;
  function trace(dir) {
    let prev = 0, prevGap = origin.y - t.heightAt(origin.x, origin.z);
    if (prevGap < 0) prevGap = 0.01;
    let step = 4;
    for (let d = step; d < FAR; d += step) {
      const x = origin.x + dir.x * d, y = origin.y + dir.y * d, z = origin.z + dir.z * d;
      const gap = y - t.heightAt(x, z);
      if (gap <= 0) {
        let lo = prev, hi = d;
        for (let k = 0; k < 24; k++) {
          const m = (lo + hi) * 0.5;
          const g = origin.y + dir.y * m - t.heightAt(origin.x + dir.x * m, origin.z + dir.z * m);
          if (g <= 0) hi = m; else lo = m;
        }
        const m = (lo + hi) * 0.5;
        const hx = origin.x + dir.x * m, hz = origin.z + dir.z * m;
        const h = t.heightAt(hx, hz);
        const rr = Math.hypot(hx - t.cx, hz - t.cz);
        /* The mainland is a mesh, not part of `heightAt` — nothing plants or
         * drives there, so it was never worth putting in the height function.
         * A ray descending towards the water that reaches the sea beyond the
         * mainland's shoreline is a ray the mainland stopped. */
        if (h <= waterY + 0.25 && t.mainlandR && rr >= t.mainlandR) {
          return {kind: 'main', dist: m, r: rr};
        }
        return {kind: h > waterY + 0.25 ? 'land' : 'sea', dist: m, r: rr};
      }
      prev = d; prevGap = gap;
      step = Math.min(60, Math.max(4, gap * 0.55));
    }
    return {kind: 'sky', dist: FAR, r: 0};
  }

  const cells = [];
  const rows = [];
  let land = 0, sea = 0, sky = 0, main = 0;
  const H = Math.round(G * 9 / 16);
  for (let j = 0; j < H; j++) {
    const row = [];
    for (let i = 0; i < G; i++) {
      const ndcX = ((i + 0.5) / G) * 2 - 1;
      const ndcY = 1 - ((j + 0.5) / H) * 2;
      const cxr = ndcX * tx, cyr = ndcY * ty;
      let vx = bx.x * cxr + by.x * cyr - bz.x;
      let vy = bx.y * cxr + by.y * cyr - bz.y;
      let vz = bx.z * cxr + by.z * cyr - bz.z;
      const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
      const hit = trace({x: vx, y: vy, z: vz});
      row.push(hit.kind === 'land' ? 'L' : hit.kind === 'sea' ? 'S'
             : hit.kind === 'main' ? 'M' : '.');
      if (hit.kind === 'land') land++;
      else if (hit.kind === 'sea') sea++;
      else if (hit.kind === 'main') main++; else sky++;
      cells.push(hit);
    }
    rows.push(row.join(''));
  }

  const total = G * H;
  const at = (i, j) => rows[j][i];
  /* Which of the four frame edges carry sea, and how much of each. An island
   * from a camera standing on it shows sea on the far edge and BOTH sides; a
   * coastal site shows it on the far edge only. */
  const edge = (name, pts) => {
    let s = 0;
    for (const [i, j] of pts) if (at(i, j) === 'S') s++;
    return [name, +(s / pts.length).toFixed(3)];
  };
  const left = [], right = [], top = [], bottom = [];
  for (let j = 0; j < H; j++) { left.push([0, j]); right.push([G - 1, j]); }
  for (let i = 0; i < G; i++) { top.push([i, 0]); bottom.push([i, H - 1]); }
  const edges = Object.fromEntries([edge('left', left), edge('right', right),
                                    edge('top', top), edge('bottom', bottom)]);
  /* And in the outer band, not just the one-pixel edge — a coastline that is
   * about to leave the frame is as good as one that has. */
  const band = (i0, i1, j0, j1) => {
    let s = 0, n = 0;
    for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) { n++; if (at(i, j) === 'S') s++; }
    return +(s / n).toFixed(3);
  };
  const k = Math.max(2, Math.round(G * 0.12));
  const kv = Math.max(2, Math.round(H * 0.12));

  /* Where the waterline sits in the frame, as a fraction from the top, on the
   * centre column and on both side columns. If the side columns cross LOWER
   * than the centre, the land is narrowing towards the viewer on both sides,
   * which is the shape of an island seen from on it. */
  const cross = (i) => {
    for (let j = H - 1; j >= 0; j--) if (at(i, j) === 'S') return +( (j + 1) / H ).toFixed(3);
    return null;
  };
  return {
    waterY: +waterY.toFixed(1),
    islandR: Math.round(t.islandR), siteRadial: Math.round(t.siteRadial),
    coastWobble: Math.round(t.coastWobble),
    landPct: +(land / total * 100).toFixed(1),
    seaPct: +(sea / total * 100).toFixed(1),
    skyPct: +(sky / total * 100).toFixed(1),
    mainlandPct: +(main / total * 100).toFixed(1),
    mainlandR: t.mainlandR ? Math.round(t.mainlandR) : null,
    edges,
    bands: {left: band(0, k, 0, H), right: band(G - k, G, 0, H),
            top: band(0, G, 0, kv), bottom: band(0, G, H - kv, H)},
    waterlineAt: {leftEdge: cross(0), quarter: cross(Math.round(G * 0.25)),
                  centre: cross(Math.round(G * 0.5)),
                  threeQuarter: cross(Math.round(G * 0.75)),
                  rightEdge: cross(G - 1)},
    ascii: rows.filter((_, j) => j % 2 === 0).map(r => r.replace(/(.)/g, '$1')),
  };
}, {G: grid});

out.errors = errors;
const ascii = out.ascii; delete out.ascii;
console.log(JSON.stringify(out, null, 2));
if (ascii) { console.log('\n--- frame (L land, S sea, M mainland, . sky) ---'); for (const r of ascii) console.log(r); }
await browser.close();
