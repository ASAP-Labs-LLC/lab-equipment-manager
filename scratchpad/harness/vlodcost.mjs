/* What the furthest vegetation LOD actually costs, and what it saves.
 * Boots once, inventories the vegetation meshes, then measures frame time with
 * each LOD hidden in turn. Hiding is the only honest ablation available: the
 * ranges are compiled into the placement, so re-ranging rebuilds the world and
 * changes more than one thing at a time. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const CAM = process.argv[2] || 'far';
const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${CAM}&time=9&weather=clear&hud=0&quality=ultra`;
const WD = process.env.HOME + '/LAB-lem/LEM Web Server/static/world';
const st = () => fs.readdirSync(WD).filter(f=>f.endsWith('.js')).map(f=>f+fs.statSync(WD+'/'+f).mtimeMs).join();
const before = st();

const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(3000);

const inv = await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  const out = [];
  const root = (veg && veg.group) || window.__lemWorld.scene;
  root.traverse(o => {
    if (!o.isInstancedMesh && !o.isMesh) return;
    const g = o.geometry, idx = g.index ? g.index.count : g.attributes.position.count;
    const per = idx / 3, n = o.isInstancedMesh ? o.count : 1;
    out.push({name: o.name || '(unnamed)', instances: n,
              trisEach: +per.toFixed(1), trisTotal: Math.round(per * n),
              visible: o.visible});
  });
  return out.sort((a,b)=>b.trisTotal-a.trisTotal);
});
console.log('vegetation meshes, costliest first:');
for (const m of inv.slice(0,12))
  console.log(`  ${m.name.padEnd(26)} ${String(m.instances).padStart(7)} inst ` +
              `x ${String(m.trisEach).padStart(6)} tris = ${String(m.trisTotal).padStart(9)}`);
console.log('  TOTAL veg triangles:', inv.reduce((s,m)=>s+m.trisTotal,0).toLocaleString());

async function fps(hide) {
  await p.evaluate(pat => {
    const veg = window.__lemWorld.subsystems.get('vegetation');
    const root = (veg && veg.group) || window.__lemWorld.scene;
    root.traverse(o => {
      if (!o.isInstancedMesh && !o.isMesh) return;
      if (o.__origVis === undefined) o.__origVis = o.visible;
      o.visible = pat ? !(new RegExp(pat,'i')).test(o.name||'') && o.__origVis
                      : o.__origVis;
    });
  }, hide);
  await p.waitForTimeout(700);
  return await p.evaluate(() => new Promise(res => {
    const f=[]; let last=performance.now(); const stop=last+2600;
    const tick=n=>{f.push(n-last); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else {f.sort((a,b)=>a-b);
        const stats=window.__lemWorld.stats();
        res({ms:+f[f.length>>1].toFixed(2), fps:Math.round(1000/f[f.length>>1]),
             draws:stats.drawCalls, tris:stats.triangles});}};
    requestAnimationFrame(tick);
  }));
}

const base = await fps(null);
console.log(`\nbaseline            ${base.fps} fps  ${base.ms} ms  ${base.draws} draws  ${base.tris.toLocaleString()} tris`);
for (const pat of ['grove','card','clump','crown']) {
  const r = await fps(pat);
  if (r.tris === base.tris) { console.log(`hide /${pat}/ — matched nothing`); continue; }
  console.log(`hide /${pat}/`.padEnd(20) +
    `${r.fps} fps  ${r.ms} ms  ${r.draws} draws  ${r.tris.toLocaleString()} tris   ` +
    `saves ${(base.ms-r.ms).toFixed(2)} ms (${(100*(base.ms-r.ms)/base.ms).toFixed(1)}%)`);
}
await fps(null);
console.log('\nbuild stable during measurement:', before === st());
await b.close();
