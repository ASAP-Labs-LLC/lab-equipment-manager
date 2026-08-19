import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail'), T = w.subsystems.get('terrain');
  const out = {routes: [], tracks: []};
  // per-track radius, measured on the frames themselves at 3m chord
  for (const t of rail.tracks) {
    if (!t.frames) continue;
    const f = t.frames, n = f.count, st = f.step;
    const m = Math.max(1, Math.round(3/st));
    let minR = Infinity, minS = 0, maxG = 0, maxGS = 0;
    const worst = [];
    for (let i=m; i<n-m; i++) {
      const ax=f.pos[(i-m)*3]-f.pos[i*3], az=f.pos[(i-m)*3+2]-f.pos[i*3+2];
      const cx=f.pos[(i+m)*3]-f.pos[i*3], cz=f.pos[(i+m)*3+2]-f.pos[i*3+2];
      const cr=Math.abs(ax*cz-az*cx);
      const la=Math.hypot(ax,az), lc=Math.hypot(cx,cz), lb=Math.hypot(ax-cx,az-cz);
      if (cr>1e-9){ const R=(la*lc*lb)/(2*cr); if(R<minR){minR=R;minS=i*st;} }
      const g=Math.abs(f.pos[(i+1)*3+1]-f.pos[i*3+1])/st;
      if(g>maxG){maxG=g;maxGS=i*st;}
    }
    out.tracks.push({name:t.name, len:+t.length.toFixed(0), minR:+minR.toFixed(0), atS:+minS.toFixed(0),
                     maxGradePct:+(maxG*100).toFixed(1), atGS:+maxGS.toFixed(0),
                     renderFrom:+(t.renderFrom||0).toFixed(0), renderTo:+Math.min(t.renderTo,t.length).toFixed(0)});
  }
  for (const st of w.plan.stations.slice(0,2)) {
    const r = rail.route(st.uid);
    if (!r) continue;
    const cyc = rail.cycle(st.uid);
    const N=400, pts=[]; for(let i=0;i<=N;i++) pts.push(r.getPointAt(i/N));
    const step = r.length/N;
    const bad=[];
    for (let i=1;i<N;i++){
      const a=pts[i-1],mq=pts[i],c=pts[i+1];
      const ax=a.x-mq.x,az=a.z-mq.z,cx=c.x-mq.x,cz=c.z-mq.z;
      const cr=Math.abs(ax*cz-az*cx);
      const la=Math.hypot(ax,az),lc=Math.hypot(cx,cz),lb=Math.hypot(a.x-c.x,a.z-c.z);
      const R = cr>1e-9 ? (la*lc*lb)/(2*cr) : Infinity;
      const g = Math.abs(mq.y-a.y)/step*100;
      if (R<90 || g>2.5) bad.push({s:+(i*step).toFixed(0), R:+R.toFixed(0), g:+g.toFixed(1),
                                   x:+mq.x.toFixed(0), z:+mq.z.toFixed(0)});
    }
    out.routes.push({uid:st.uid, len:+r.length.toFixed(0), bad: bad.slice(0,40), badCount:bad.length,
                     segs: cyc?.segments?.map(s=>`${s.track}:${s.s0.toFixed(0)}-${s.s1.toFixed(0)}`)});
  }
  return out;
}), null, 1));
await b.close();
