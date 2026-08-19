/* Is alignment.mjs's worstCuttingM regression the span clip, or rail re-planning?
 * Rebuild terrain's earthworks index in the page with every clip flag cleared,
 * force a re-grade, and re-walk the same three routes. No file edit. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(12000);
const walk = () => p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const r = window.__lemWorld.subsystems.get('rail');
  const out = [];
  for (const tr of r.tracks) {
    const f = tr.frames; if (!f) continue;
    let inCut = 0, worst = 0;
    for (let i = 0; i < f.count; i++) {
      const x = f.pos[i*3], y = f.pos[i*3+1], z = f.pos[i*3+2];
      const g = t.heightAt(x, z);
      const d = y - g;                       // railhead above ground
      if (d < -0.5) { inCut++; if (d < worst) worst = d; }
    }
    out.push({name: tr.name, inCut, worst: +worst.toFixed(2), pts: f.count});
  }
  return out;
});
const before = await walk();
const clipped = await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const E = t._ework; if (!E || !E.ec) return -1;
  let n = 0; for (let i = 0; i < E.ec.length; i++) if (E.ec[i]) { n++; E.ec[i] = 0; }
  t._teardownMeshes(); t._buildField(); t._buildCore();
  t._buildRing(t.ringSize, t.ringSeg, t.coreSize, 40);
  return n;
});
await p.waitForTimeout(3000);
const after = await walk();
console.log(JSON.stringify({clipFlagsCleared: clipped, withClip: before, withoutClip: after}, null, 1));
await b.close();
