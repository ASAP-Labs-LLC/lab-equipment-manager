/* pwmap.mjs — read back a rail material's generated maps and report the actual
 * sRGB values in each band. Guessing at what a paint() closure produced is how
 * two rounds of "it should be mid brown" shipped charcoal. Owned by rail. */
import {chromium} from 'playwright';
const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail&time=16&hud=0&quality=ultra';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 800, height: 500}});
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const seen = new Map();
  rail.root.traverse(o => {
    if (!o.material?.map?.image) return;
    const key = o.material.uuid;
    if (seen.has(key)) return;
    seen.set(key, {mat: o.material, name: o.name || o.type,
                   count: o.count ?? null});
  });
  const res = [];
  for (const [, e] of seen) {
    const img = e.mat.map.image;
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    c.getContext('2d').drawImage(img, 0, 0);
    const d = c.getContext('2d').getImageData(0, 0, img.width, img.height).data;
    /* per horizontal band of 1/16, mean rgb */
    const bands = [];
    for (let k = 0; k < 16; k++) {
      let r = 0, g = 0, bl = 0, n = 0;
      const y0 = Math.floor(k * img.height / 16), y1 = Math.floor((k + 1) * img.height / 16);
      for (let y = y0; y < y1; y++) for (let x = 0; x < img.width; x += 3) {
        const i = (y * img.width + x) * 4;
        r += d[i]; g += d[i + 1]; bl += d[i + 2]; n++;
      }
      bands.push([+(r / n / 255).toFixed(3), +(g / n / 255).toFixed(3),
                  +(bl / n / 255).toFixed(3)]);
    }
    res.push({size: img.width, bands, count: e.count,
              metal: e.mat.metalness, rough: e.mat.roughness});
  }
  return res;
});
console.log(JSON.stringify(out, null, 1));
await b.close();
