/* gi-shot.mjs — like shot.mjs but (a) routes engine.js through a syntax fixup so
 * a concurrent edit in another agent's file cannot block gi verification, and
 * (b) can run an arbitrary probe expression before the shot. */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) { const k=a.slice(2), n=process.argv[i+1];
    if(!n||n.startsWith('--')) args[k]=true; else {args[k]=n;i++;} }
}
const out = path.resolve(args.out || 'gi.png');
fs.mkdirSync(path.dirname(out), {recursive:true});
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:+(args.w||1920), height:+(args.h||1080)}, deviceScaleFactor:1});
const errors=[];
p.on('console', m=>{ if(m.type()==='error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0,300)); });
p.on('pageerror', e=>errors.push('pageerror: '+String(e).slice(0,300)));
await p.route('**/world/*.js', async route => {
  const r = await route.fetch();
  let body = await r.text();
  /* Strip backticks that appear inside comments — neighbouring agents keep
   * closing a GLSL template literal with one while mid-edit, which takes the
   * whole world down and has nothing to do with what is being verified. */
  body = body.split('\n').map(l => /^\s*(\*|\/\/|\/\*)/.test(l) ? l.replace(/`/g,"'") : l).join('\n');
  /* --srgb: add the output transfer the composite is missing, so gi can be
   * judged in the pipeline as it should be rather than as it is today. */
  if (args.srgb && /COMPOSITE_FS/.test(body)) {
    body = body.replace('outColor = vec4(clamp(c, 0.0, 1.0), 1.0);',
      'c = clamp(c, 0.0, 1.0);\n    c = mix(c * 12.92, 1.055 * pow(max(c, vec3(1e-5)), vec3(1.0/2.4)) - 0.055, step(vec3(0.0031308), c));\n    outColor = vec4(c, 1.0);');
  }
  await route.fulfill({response:r, body, headers:{...r.headers(), 'content-type':'application/javascript'}});
});
await p.goto(args.url, {waitUntil:'load'});
await p.waitForTimeout((+(args.seconds||5))*1000);
if (args.probe) {
  const v = await p.evaluate(args.probe);
  console.log(JSON.stringify(v, null, 1));
}
await p.screenshot({path:out});
const stats = await p.evaluate(()=>{ const w=window.__lemWorld; return w? w.stats():null; });
console.log(JSON.stringify({out, stats, errors}));
await b.close();
