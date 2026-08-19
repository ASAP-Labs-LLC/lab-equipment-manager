/* pr-boot.mjs — what props cost the TIME TO FIRST FRAME, which is the budget
 * they can actually threaten. eg-boot.mjs's ladder is a fixed list that does not
 * include props, so this is the same method with props on the end of it, plus
 * the pair that matters: the full stack with and without props, interleaved so
 * a machine that is getting busier cannot masquerade as a regression.
 *
 *   node pr-boot.mjs [repeats]
 */
import {chromium} from 'playwright';
const REPEATS = parseInt(process.argv[2] || '5', 10);
const FULL = 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});

async function once(mods) {
  const p = await (await b.newContext({viewport: {width: 1920, height: 1080}})).newPage();
  const t0 = Date.now();
  await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + mods +
    '&cam=far&time=9&weather=clear&hud=0&quality=ultra',
    {waitUntil: 'load', timeout: 90000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  const ms = Date.now() - t0;
  let bm = null;
  try {
    bm = await p.evaluate(() =>
      window.__lemWorld.subsystems.get('props')?.buildMs || null);
  } catch { /* props not loaded */ }
  await p.context().close();
  return {ms, bm};
}

/* INTERLEAVED, not one block then the other. A run of five with-props followed
 * by five without measures the machine's load curve as much as the module. */
const withP = [], noP = [];
let lastBm = null;
for (let i = 0; i < REPEATS; i++) {
  const a = await once(FULL);              noP.push(a.ms);
  const c = await once(FULL + ',props');   withP.push(c.ms); lastBm = c.bm || lastBm;
  console.log('  pass ' + (i + 1) + ':  without ' + a.ms + ' ms   with ' + c.ms +
    ' ms   delta ' + (c.ms - a.ms) + ' ms');
}
const med = a => { a = [...a].sort((x, y) => x - y); return a[a.length >> 1]; };
const paired = withP.map((v, i) => v - noP[i]);
console.log('\nmedian without props   ' + med(noP) + ' ms');
console.log('median with props      ' + med(withP) + ' ms');
console.log('median PAIRED delta    ' + med(paired) + ' ms   ' +
  'spread [' + Math.min(...paired) + ', ' + Math.max(...paired) + ']');
console.log('props own build clock  ' + JSON.stringify(lastBm) +
  '   (regions = the whole-island land mask, transform and field probe)');
console.log('budget 3000 ms.');
await b.close();
