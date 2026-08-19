/* Does the aerial-perspective fog patch actually reach a compiled shader, and
 * what is the fog factor at a ladder of depths? Two rounds of critics said the
 * haze was uniform; this answers it with the shader source and the numbers. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
const res = await p.evaluate(() => {
  const w = window.__lemWorld, r = w.engine.renderer, gl = r.getContext();
  const out = {chunkPatched: !!(window.THREE_SHADERCHUNK_MARK), programs: 0, withLem: 0, sample: null, fog: null, sky: null};
  for (const prog of r.info.programs) {
    const s = gl.getShaderSource(prog.fragmentShader) || '';
    if (!/USE_FOG/.test(s)) continue;
    out.programs++;
    if (/lemTau|lemAvg|lemA/.test(s)) out.withLem++;
    if (!out.sample && /fogFactor/.test(s)) {
      const i = s.indexOf('#ifdef USE_FOG', s.indexOf('void main'));
      const j = s.indexOf('fogFactor');
      out.sample = s.slice(Math.max(0,j-900), j+700);
    }
  }
  const f = w.scene.fog;
  if (f) out.fog = {type: f.type, density: f.density, color: f.color.getHexString(),
                    r: f.color.r, g: f.color.g, b: f.color.b};
  const sky = w.ctx?.sky || window.__lemWorld.sky;
  return out;
});
console.log(JSON.stringify(res, null, 1).slice(0, 6000));
await b.close();
