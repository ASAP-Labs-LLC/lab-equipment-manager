/* vstip.mjs — what is left of the speckle once the fwidth window is gated?
 * Sweeps the far tiers' mip-dither window and alpha bias live, one page
 * session, and reports both the colour and a stipple statistic: the mean
 * absolute difference between neighbouring pixels inside a canopy crop, which
 * a screen door raises and a mass does not. */
import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2];
if (URL.includes('solo.html') && !/[?&]quality=/.test(URL)) URL += '&quality=ultra';
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/ST';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0,240)));
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(10000);

const shot = async n => { await p.waitForTimeout(1500);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };

const apply = cfg => p.evaluate(c => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const set = (m, o) => { if (!m) return;
    const L = m.userData.lem;
    if (o.sharp) L.uVegSharp.value.set(o.sharp[0], o.sharp[1]);
    if (o.dither !== undefined) L.uVegDither.value = o.dither;
    if (o.bias !== undefined) L.uVegAlphaBias.value = o.bias;
    if (o.edge !== undefined) L.uVegEdge.value = o.edge; };
  set(v.matFar, c.far || {});
  set(v.matGrove, c.grove || {});
  return 'ok';
}, cfg);

for (const [name, cfg] of [
  ['ref',        {far: {sharp: [99, 100], dither: 0.26, bias: 0.34, edge: 3.0},
                  grove: {sharp: [99, 100], dither: 0.0, bias: -0.10, edge: 2.4}}],
  ['sharp',      {far: {sharp: [1.2, 3.0]}, grove: {sharp: [1.2, 3.0]}}],
  ['sharp-d10',  {far: {dither: 0.10}}],
  ['sharp-d00',  {far: {dither: 0.0}}],
  ['sharp-d00-gb10', {grove: {bias: 0.10}}],
  ['sharp-d00-gb25', {grove: {bias: 0.25}}],
]) { await apply(cfg); await shot(name); }

console.log('errors', JSON.stringify(errs.slice(0,4)));
await b.close();
