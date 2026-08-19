/* What a grade-limited profile would cost on the alignments as they stand:
 * the least-squares profile with |dy/ds| <= g, and the cut and fill it implies. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, T = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  const out = [];
  for (const t of rail.tracks) {
    const f = t.frames; if (!f) continue;
    const n = f.count, step = f.step;
    const G = new Float64Array(n);
    for (let i=0;i<n;i++) G[i] = T.heightAt(f.pos[i*3], f.pos[i*3+2]);
    const g = 0.025, d = g*step;
    const y = Float64Array.from(G);
    for (let it=0; it<4000; it++) {
      let moved = 0;
      for (let i=0;i<n-1;i++){ const e=y[i+1]-y[i]; if (e>d){const h=(e-d)/2;y[i]+=h;y[i+1]-=h;moved=1;} else if (e<-d){const h=(-e-d)/2;y[i]-=h;y[i+1]+=h;moved=1;} }
      for (let i=n-2;i>=0;i--){ const e=y[i+1]-y[i]; if (e>d){const h=(e-d)/2;y[i]+=h;y[i+1]-=h;moved=1;} else if (e<-d){const h=(-e-d)/2;y[i]-=h;y[i+1]+=h;moved=1;} }
      if (!moved) break;
    }
    let maxCut=0, maxFill=0, sc=0, sf=0, nc=0, nf=0, worst=0;
    for (let i=0;i<n;i++){ const dd=y[i]-G[i]; if(dd<0){maxCut=Math.max(maxCut,-dd);sc-=dd;nc++;} else {maxFill=Math.max(maxFill,dd);sf+=dd;nf++;} }
    for (let i=1;i<n;i++) worst=Math.max(worst, Math.abs(y[i]-y[i-1])/step);
    // natural ground roughness
    let gmax=0; for(let i=1;i<n;i++) gmax=Math.max(gmax, Math.abs(G[i]-G[i-1])/step);
    out.push({name:t.name, len:+t.length.toFixed(0),
      groundDrop:+(Math.max(...G)-Math.min(...G)).toFixed(1), groundMaxGrade:+(gmax*100).toFixed(0),
      maxCut:+maxCut.toFixed(1), maxFill:+maxFill.toFixed(1),
      meanCut:+(sc/Math.max(1,nc)).toFixed(2), meanFill:+(sf/Math.max(1,nf)).toFixed(2),
      pctCut:+(100*nc/n).toFixed(0), fitGrade:+(worst*100).toFixed(2)});
  }
  return out;
}), null, 1));
await b.close();
