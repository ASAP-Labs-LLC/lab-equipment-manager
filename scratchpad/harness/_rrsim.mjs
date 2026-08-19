/* _rrsim.mjs — what does a SHORTER approach reserve cost, with and without
 * terrain's end-clip propagated past the last segment?
 *
 * Rebuilds terrain's grading index from the published spans in-page, so the
 * rule and the geometry are both editable, then measures the lift of natural
 * ground into whatever is left of each deck span. Validated against
 * tq-spanclip.mjs: mode base must reproduce its worstLiftNow / meanLiftNow.
 *
 *   node _rrsim.mjs --give 4       give 4 m of each deck end back to fill
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i+1];
const GIVE = parseFloat(a.give || '4');
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0,300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(6000);
const out = await p.evaluate((GIVE) => {
  const W = window.__lemWorld;
  const t = W.subsystems.get('terrain'), r = W.subsystems.get('rail');
  const E0 = t._ework;
  const spans = r.earthworks();
  const clamp = (v,x,y)=>v<x?x:(v>y?y:v);
  const SNAP = 16, STEEP = 5, ROUND = 6, BLEND = 10, TOE_K = 3;

  /* Build an index from a list of {points, kind, half, batter} exactly the way
   * terrain._setEarthworks does, but with `propagate` deciding whether the
   * end-clip flag stays on the end segment or covers the whole span. */
  function build(list, propagate) {
    const kx0=[],kz0=[],kx1=[],kz1=[],kr=[];
    for (const sp of list) {
      if (sp.kind!=='tunnel'&&sp.kind!=='viaduct'&&sp.kind!=='bridge') continue;
      const P=sp.points, n=P.length/3|0; if(n<2) continue;
      const half=Math.max(2,+sp.half||4.148);
      for(let i=0;i+1<n;i++){kx0.push(P[i*3]);kz0.push(P[i*3+2]);
        kx1.push(P[i*3+3]);kz1.push(P[i*3+5]);kr.push(half+SNAP);}
    }
    const nearStruct=(x,z)=>{for(let i=0;i<kr.length;i++){
      const vx=kx1[i]-kx0[i],vz=kz1[i]-kz0[i],wx=x-kx0[i],wz=z-kz0[i];
      const L=vx*vx+vz*vz,tt=L>1e-9?clamp((wx*vx+wz*vz)/L,0,1):0;
      const dx=wx-vx*tt,dz=wz-vz*tt; if(dx*dx+dz*dz<=kr[i]*kr[i])return true;}return false;};
    const ax=[],ay=[],az=[],bx=[],by=[],bz=[],hw=[],sc=[],sf=[],ec=[];
    let reach=24,minX=1e18,maxX=-1e18,minZ=1e18,maxZ=-1e18;
    for (const sp of list) {
      if (sp.kind==='tunnel'||sp.kind==='viaduct'||sp.kind==='bridge') continue;
      const P=sp.points,n=P.length/3|0; if(n<2) continue;
      const half=Math.max(2,+sp.half||4.148);
      const bat=+sp.batter>0?+sp.batter:(sp.kind==='fill'?1.5:1.0);
      const bCut=sp.kind==='cut'?bat:1.0, bFill=sp.kind==='fill'?bat:1.5;
      const depth=Math.abs(+sp.maxDepth||0);
      reach=Math.max(reach,half+depth*Math.max(bCut,bFill)+ROUND+BLEND);
      const e0=kr.length&&nearStruct(P[0],P[2])?1:0;
      const e1=kr.length&&nearStruct(P[(n-1)*3],P[(n-1)*3+2])?2:0;
      const last=n-2;
      for(let i=0;i+1<n;i++){
        ax.push(P[i*3]);ay.push(P[i*3+1]);az.push(P[i*3+2]);
        bx.push(P[i*3+3]);by.push(P[i*3+4]);bz.push(P[i*3+5]);
        hw.push(half);sc.push(1/bCut);sf.push(1/bFill);
        ec.push(propagate ? (e0|e1) : ((i===0?e0:0)|(i===last?e1:0)));
        minX=Math.min(minX,P[i*3],P[i*3+3]);maxX=Math.max(maxX,P[i*3],P[i*3+3]);
        minZ=Math.min(minZ,P[i*3+2],P[i*3+5]);maxZ=Math.max(maxZ,P[i*3+2],P[i*3+5]);
      }
    }
    const NS=hw.length; if(!NS) return null;
    const cell=Math.max(24,reach), x0=minX-reach-cell, z0=minZ-reach-cell;
    const nx=Math.max(1,Math.ceil((maxX+reach+cell-x0)/cell));
    const nz=Math.max(1,Math.ceil((maxZ+reach+cell-z0)/cell));
    const count=new Int32Array(nx*nz+1), lo=new Int32Array(NS*4);
    for(let i=0;i<NS;i++){
      const i0=clamp(Math.floor((Math.min(ax[i],bx[i])-reach-x0)/cell),0,nx-1);
      const i1=clamp(Math.floor((Math.max(ax[i],bx[i])+reach-x0)/cell),0,nx-1);
      const j0=clamp(Math.floor((Math.min(az[i],bz[i])-reach-z0)/cell),0,nz-1);
      const j1=clamp(Math.floor((Math.max(az[i],bz[i])+reach-z0)/cell),0,nz-1);
      lo[i*4]=i0;lo[i*4+1]=i1;lo[i*4+2]=j0;lo[i*4+3]=j1;
      for(let j=j0;j<=j1;j++)for(let k=i0;k<=i1;k++)count[j*nx+k+1]++;
    }
    for(let q=0;q<nx*nz;q++)count[q+1]+=count[q];
    const start=Int32Array.from(count), idx=new Int32Array(count[nx*nz]);
    const fl=Int32Array.from(count.subarray(0,nx*nz));
    for(let i=0;i<NS;i++){const i0=lo[i*4],i1=lo[i*4+1],j0=lo[i*4+2],j1=lo[i*4+3];
      for(let j=j0;j<=j1;j++)for(let k=i0;k<=i1;k++)idx[fl[j*nx+k]++]=i;}
    return {ax:Float32Array.from(ax),ay:Float32Array.from(ay),az:Float32Array.from(az),
      bx:Float32Array.from(bx),by:Float32Array.from(by),bz:Float32Array.from(bz),
      hw:Float32Array.from(hw),sc:Float32Array.from(sc),sf:Float32Array.from(sf),
      ec:Int8Array.from(ec),cell,x0,z0,nx,nz,start,idx,reach,segments:NS,
      clipped:ec.reduce((s,v)=>s+(v?1:0),0)};
  }
  const smin=(p1,p2,kk)=>{if(kk<=1e-6)return Math.min(p1,p2);
    const hh=Math.max(0,kk-Math.abs(p1-p2))/kk;return Math.min(p1,p2)-hh*hh*kk*0.25;};
  function grade(E,h,x,z){
    const ix=Math.floor((x-E.x0)/E.cell), iz=Math.floor((z-E.z0)/E.cell);
    if(ix<0||iz<0||ix>=E.nx||iz>=E.nz)return h;
    const bk=iz*E.nx+ix; let ceil=Infinity,floor=-Infinity,near=1e9;
    for(let q=E.start[bk],e=E.start[bk+1];q<e;q++){
      const i=E.idx[q];
      const vx=E.bx[i]-E.ax[i],vz=E.bz[i]-E.az[i],wx=x-E.ax[i],wz=z-E.az[i];
      const L=vx*vx+vz*vz, tr=L>1e-9?(wx*vx+wz*vz)/L:0, tt=clamp(tr,0,1);
      const dx=wx-vx*tt,dz=wz-vz*tt; let f=Math.hypot(dx,dz)-E.hw[i];
      const c=E.ec[i];
      if(c&&(tr<0?(c&1):(tr>1?(c&2):0))) f+=(tr<0?-tr:tr-1)*Math.sqrt(L)*STEEP;
      if(f>E.reach)continue; if(f<near)near=f;
      const yf=E.ay[i]+(E.by[i]-E.ay[i])*tt;
      if(f<=0){if(yf<ceil)ceil=yf;if(yf>floor)floor=yf;continue;}
      const fe=(f*f)/(f+ROUND);
      const cc=yf+fe*E.sc[i]; if(cc<ceil)ceil=cc;
      const ff=yf-fe*E.sf[i]; if(ff>floor)floor=ff;
    }
    if(near>1e8)return h;
    const g=t._railGuard(x,z); if(g<=0.001)return h;
    const kk=Math.min(1,Math.max(0,near)/ROUND)*TOE_K;
    let y=h; if(ceil<Infinity)y=smin(y,ceil,kk);
    if(floor>-Infinity)y=-smin(-y,-floor,kk);
    return g>=0.999?y:h+(y-h)*g;
  }
  const preRail=(x,z)=>{const bb=t._baseHeight(x,z);
    if(!t.features||!t.design)return bb;
    return t._gradeTo(bb,t._designAt(x,z),t._distances(x,z,null));};

  /* variant geometry: give GIVE metres of each deck end back to the fill */
  function shrink(list, give) {
    const outL=[]; const bykey=new Map();
    for(const sp of list) outL.push(sp);
    const res=[];
    for(const sp of outL){
      if(sp.kind!=='viaduct'&&sp.kind!=='bridge'){res.push(sp);continue;}
      const P=sp.points,n=P.length/3|0, step=sp.step||1.5;
      const m=Math.min(Math.floor((n-2)/2), Math.round(give/step));
      if(m<1){res.push(sp);continue;}
      const cut=(a0,a1)=>{const q=new Float32Array((a1-a0+1)*3);
        for(let i=a0;i<=a1;i++){q[(i-a0)*3]=P[i*3];q[(i-a0)*3+1]=P[i*3+1];q[(i-a0)*3+2]=P[i*3+2];}return q;};
      res.push({...sp, kind:'fill', batter:1.5, maxDepth:6.0, points:cut(0,m)});
      res.push({...sp, points:cut(m,n-1-m)});
      res.push({...sp, kind:'fill', batter:1.5, maxDepth:6.0, points:cut(n-1-m,n-1)});
      res[res.length-2]._deck=true;
    }
    return res;
  }

  /* variant: publish the last R metres of a fill/cut span that abuts a deck as
   * ONE-SEGMENT spans, so every one of them is its own span's first AND last
   * segment and therefore carries terrain's end-clip flag. */
  function shred(list, R) {
    const decks = list.filter(q=>q.kind==='viaduct'||q.kind==='bridge');
    const nearDeck=(x,z)=>{for(const d of decks){const P=d.points,n=P.length/3|0;
      for(let i=0;i<n;i++){const dx=x-P[i*3],dz=z-P[i*3+2];
        if(dx*dx+dz*dz<=(4.148+SNAP)*(4.148+SNAP))return true;}}return false;};
    const res=[];
    for(const sp of list){
      if(sp.kind==='viaduct'||sp.kind==='bridge'||sp.kind==='tunnel'||sp.kind==='grade'){res.push(sp);continue;}
      const P=sp.points,n=P.length/3|0,step=sp.step||1.5;
      const m=Math.min(n-1,Math.round(R/step));
      const head=nearDeck(P[0],P[2]), tail=nearDeck(P[(n-1)*3],P[(n-1)*3+2]);
      if((!head&&!tail)||n<3){res.push(sp);continue;}
      const a0 = head?m:0, a1 = tail?n-1-m:n-1;
      const cut=(x,y)=>{const q=new Float32Array((y-x+1)*3);
        for(let i=x;i<=y;i++){q[(i-x)*3]=P[i*3];q[(i-x)*3+1]=P[i*3+1];q[(i-x)*3+2]=P[i*3+2];}return q;};
      if(head)for(let i=0;i<m;i++)res.push({...sp,points:cut(i,i+1)});
      if(a1>a0)res.push({...sp,points:cut(a0,a1)});
      if(tail)for(let i=n-1-m;i<n-1;i++)res.push({...sp,points:cut(i,i+1)});
    }
    return res;
  }
  /* variant: the last R metres against a deck are a RETAINED approach — their
   * own span, declared with a wall batter instead of 1:1.5. */
  function retained(list, R, bat) {
    const decks = list.filter(q=>q.kind==='viaduct'||q.kind==='bridge');
    const nearDeck=(x,z)=>{for(const d of decks){const P=d.points,n=P.length/3|0;
      for(let i=0;i<n;i++){const dx=x-P[i*3],dz=z-P[i*3+2];
        if(dx*dx+dz*dz<=(4.148+SNAP)*(4.148+SNAP))return true;}}return false;};
    const res=[];
    for(const sp of list){
      if(sp.kind!=='fill'){res.push(sp);continue;}
      const P=sp.points,n=P.length/3|0,step=sp.step||1.5;
      const m=Math.min(n-1,Math.round(R/step));
      const head=nearDeck(P[0],P[2]), tail=nearDeck(P[(n-1)*3],P[(n-1)*3+2]);
      if((!head&&!tail)||n<3){res.push(sp);continue;}
      const a0=head?m:0, a1=tail?n-1-m:n-1;
      const cut=(x,y)=>{const q=new Float32Array((y-x+1)*3);
        for(let i=x;i<=y;i++){q[(i-x)*3]=P[i*3];q[(i-x)*3+1]=P[i*3+1];q[(i-x)*3+2]=P[i*3+2];}return q;};
      if(head)res.push({...sp,batter:bat,points:cut(0,m)});
      if(a1>a0)res.push({...sp,points:cut(a0,a1)});
      if(tail)res.push({...sp,batter:bat,points:cut(n-1-m,n-1)});
    }
    return res;
  }
  const variants = {
    base:       {list: spans, prop: false},
    propagated: {list: spans, prop: true},
    shrunk:     {list: shrink(spans, GIVE), prop: false},
    both:       {list: shrink(spans, GIVE), prop: true},
    shred9:     {list: shred(spans, 9), prop: false},
    shred18:    {list: shred(spans, 18), prop: false},
    retained9:  {list: retained(spans, 9, 0.35), prop: false},
    shred18shrunk: {list: shred(shrink(spans, GIVE), 18), prop: false},
    retShrunk:  {list: retained(shrink(spans, GIVE), 9, 0.35), prop: false},
    retShrunk2: {list: retained(shrink(spans, GIVE), 12, 0.20), prop: false},
    retOnly12:  {list: retained(spans, 12, 0.20), prop: false},
  };
  const res = {};
  for (const [nm, v] of Object.entries(variants)) {
    const E = build(v.list, v.prop);
    const rows = [];
    let deckM = 0;
    for (const sp of v.list) {
      if (sp.kind!=='viaduct'&&sp.kind!=='bridge') continue;
      const P=sp.points,n=P.length/3|0, step=sp.step||1.5;
      deckM += (n-1)*step;
      let worst=-1e9,sum=0;
      for(let k=0;k<n;k++){
        const x=P[k*3],z=P[k*3+2]; const nat=preRail(x,z);
        const lift=grade(E,nat,x,z)-nat;
        if(lift>worst)worst=lift; sum+=lift;
      }
      rows.push({from:+sp.from.toFixed(1),to:+sp.to.toFixed(1),track:sp.track,
                 pts:n, worst:+worst.toFixed(2), mean:+(sum/n).toFixed(2)});
    }
    res[nm]={segs:E.segments, clipped:E.clipped, deckM:+deckM.toFixed(1), rows};
  }
  return {liveSegs: E0.segments, liveClipped: E0.clipped, res};
}, GIVE);
console.log('live index:', out.liveSegs, 'segments,', out.liveClipped, 'clipped');
for (const [nm, v] of Object.entries(out.res)) {
  console.log(`\n--- ${nm}: ${v.segs} graded segs, ${v.clipped} clipped, declared deck ${v.deckM} m`);
  for (const r of v.rows)
    console.log(`   ${r.track} ${r.from}-${r.to} (${r.pts} pts)  worstLift ${r.worst}  meanLift ${r.mean}`);
}
await b.close();
