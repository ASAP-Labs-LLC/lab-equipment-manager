import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail'), terrain = w.subsystems.get('terrain');
  const out = {routes: []};
  for (const st of w.plan.stations.slice(0,3)) {
    const r = rail.route ? rail.route(st.uid) : null;
    if (!r || !r.getPointAt) continue;
    const len = r.totalLength || r.len || (r.getLength && r.getLength());
    const N = 400, pts = [];
    for (let i=0;i<=N;i++) pts.push(r.getPointAt(i/N));
    // curvature: radius through three consecutive samples
    let minR = Infinity, sharp = 0, maxGrade = 0, gradeOver2 = 0;
    const step = len / N;
    for (let i=1;i<pts.length-1;i++) {
      const a=pts[i-1], m=pts[i], c=pts[i+1];
      const ax=a.x-m.x, az=a.z-m.z, cx=c.x-m.x, cz=c.z-m.z;
      const cross = Math.abs(ax*cz - az*cx);
      const la=Math.hypot(ax,az), lc=Math.hypot(cx,cz), lb=Math.hypot(a.x-c.x,a.z-c.z);
      if (cross > 1e-9) {
        const R = (la*lc*lb)/(2*cross);
        if (R < minR) minR = R;
        if (R < 150) sharp++;
      }
      const g = Math.abs(m.y - a.y) / Math.max(1e-6, step) * 100;
      if (g > maxGrade) maxGrade = g;
      if (g > 2) gradeOver2++;
    }
    // how much earth is under the track vs cut through it
    let above=0, below=0, worstAbove=0, worstBelow=0;
    for (const q of pts) {
      const g = terrain && terrain.heightAt ? terrain.heightAt(q.x,q.z) : null;
      if (g===null || !isFinite(g)) continue;
      const d = q.y - g;
      if (d > 0.9) { above++; worstAbove = Math.max(worstAbove, d); }
      if (d < -0.3) { below++; worstBelow = Math.min(worstBelow, d); }
    }
    out.routes.push({uid: st.uid, lengthM: Math.round(len),
      minRadiusM: Math.round(minR), samplesUnder150m: sharp,
      maxGradePct: +maxGrade.toFixed(2), samplesOver2pct: gradeOver2,
      onEmbankment: above, worstEmbankmentM: +worstAbove.toFixed(1),
      inCutting: below, worstCuttingM: +worstBelow.toFixed(1), samples: pts.length});
  }
  return out;
}), null, 1));
await b.close();
