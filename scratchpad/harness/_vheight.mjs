/* Are stems planted on the ground terrain actually built, or on something else?
 * "Trees above the tunnels ... think they are level with the rail." */
import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=far&time=16&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(URL,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, veg=w.subsystems.get('vegetation'), g=w.ctx.ground;
  const spans=(w.ctx.railEarthworks||[]).filter(s=>s.kind==='tunnel');
  const nearTunnel=(x,z)=>{for(const s of spans){const pts=s.points,n=pts.length/3;
    for(let i=0;i<n;i++){const dx=pts[i*3]-x,dz=pts[i*3+2]-z;if(dx*dx+dz*dz<80*80)return true;}}return false;};
  let worst=0,sum=0,n=0,wt=0,nt=0,st=0,over1=0,over3=0;
  /* The MATRIX, not the scatter's own record of where it thought the ground
   * was: what stands in the frame is the instance transform. `+0.35` is the
   * tree tier's own sink. */
  for(const e of veg.trees||[]) for(let i=0;i<e.list.length;i++){
    const x=e.xs[i],z=e.zs[i];
    const d=Math.abs(e.mats[i*16+13]+0.35-g(x,z)); sum+=d; n++; if(d>worst)worst=d;
    if(d>1)over1++; if(d>3)over3++;
    if(nearTunnel(x,z)){nt++; st+=d; if(d>wt)wt=d;}
  }
  let cw=0,cn=0,cover=0;
  for(const c of veg.clutter||[]) for(let i=0;i<c.count;i++){
    const d=Math.abs(c.mats[i*16+13]-g(c.xs[i],c.zs[i])); cn++; if(d>cw)cw=d; if(d>1)cover++;
  }
  let sw=0,sn=0,sover=0;
  for(const s2 of veg.sward||[]) for(let i=0;i<s2.count;i++){
    const d=Math.abs(s2.mats[i*16+13]-g(s2.xs[i],s2.zs[i])); sn++; if(d>sw)sw=d; if(d>1)sover++;
  }
  /* clutter/sward carry an intended SINK (0.12 + slope*size*0.55 for a bush,
   * up to 1.5 m for a 15.5 m sward card on a hillside), so their |matY-ground|
   * is that sink and not an error. The trees' 0.35 is a constant, which is why
   * the tree row is the one that reads as zero when it is right. */
  return {reseat: veg._reseatStats || null, stems:n, meanAbsErrM:+(sum/n).toFixed(4), worstAbsErrM:+worst.toFixed(3),
          stemsOver1m:over1, stemsOver3m:over3,
          tunnelSpans:spans.length, stemsWithin80mOfABore:nt,
          tunnelMeanAbsErrM:nt?+(st/nt).toFixed(4):null, tunnelWorstAbsErrM:+wt.toFixed(3),
          clutter:{n:cn,worst:+cw.toFixed(2),over1:cover},
          sward:{n:sn,worst:+sw.toFixed(2),over1:sover}};
}),null,1));
await b.close();
