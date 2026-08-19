/* probe11 — ghost consist (bench gone from the floor) fouling live workings. */
import {chromium} from 'playwright';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport:{width:1280,height:720}});
await page.goto(url,{waitUntil:'load',timeout:60000});
await page.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await page.waitForTimeout(3500);
await page.evaluate(() => {
  const w = window.__lemWorld;
  const F = []; const seen = new Set();
  window.__gf = F;
  const ptAt=(r,s,o)=>{const C=r.C,n=r.n;if(!C||!n)return null;let t;if(r.closed){const L=C[n-1]||1;t=s-Math.floor(s/L)*L;}else t=Math.min(C[n-1],Math.max(0,s));let lo=0,hi=n-1;while(hi-lo>1){const m=(lo+hi)>>1;if(C[m]<=t)lo=m;else hi=m;}const g=C[hi]-C[lo]||1,k=(t-C[lo])/g,a=lo*3,b=hi*3,P=r.P;o.x=P[a]+(P[b]-P[a])*k;o.y=P[a+1]+(P[b+1]-P[a+1])*k;o.z=P[a+2]+(P[b+2]-P[a+2])*k;return o;};
  const body=c=>{const r=c.route;if(!r||!r.P)return null;const p=[];for(let d=0;d<=(c.length||24);d+=4){const o={x:0,y:0,z:0};if(!ptAt(r,c.s-d,o))return null;p.push(o);}return p;};
  const tick=()=>{
    const T=w.subsystems&&w.subsystems.get('trains');
    if(T&&T.consists){
      const onFloor=new Set((w.plan?.stations||[]).map(s=>s.uid));
      const live=T.consists.filter(c=>c&&c.group&&c.group.visible&&c.route);
      const B=new Map(); for(const c of live){const b=body(c); if(b)B.set(c,b);}
      for(let i=0;i<live.length;i++)for(let j=i+1;j<live.length;j++){
        const a=live[i],b=live[j],ba=B.get(a),bb=B.get(b); if(!ba||!bb)continue;
        let d2=Infinity; for(const pa of ba)for(const pb of bb){const dx=pa.x-pb.x,dy=pa.y-pb.y,dz=pa.z-pb.z;const q=dx*dx+dy*dy+dz*dz;if(q<d2)d2=q;}
        const d=Math.sqrt(d2);
        if(d<4.2){
          const ghost=(a.uid&&!onFloor.has(a.uid))||(b.uid&&!onFloor.has(b.uid));
          const cross=a.line!==b.line;
          const key=`${a.slot}/${b.slot}|${ghost}|${cross}`;
          if(!seen.has(key)){seen.add(key);
            F.push({key,d:+d.toFixed(2),ghost,cross,
              a:{slot:a.slot,uid:a.uid,state:a.state,line:a.line,holds:a.holds?[...a.holds]:[]},
              b:{slot:b.slot,uid:b.uid,state:b.state,line:b.line,holds:b.holds?[...b.holds]:[]}});}
        }
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
const boot = await page.evaluate(()=> (window.__lemWorld.plan?.stations||[]).map(s=>s.uid));
for(const u of boot) await page.evaluate(x=>window.__lemWorld.parse(x,'L'),u);
await page.waitForTimeout(2000);   // trains out on the OLD railway
// relayout to a completely different geometry: one long file
const fleet=[]; for(let i=0;i<7;i++) fleet.push([`nb-${i}`,`NB ${i}`,[0,i*2.05]]);
await page.evaluate(f=>window.__lemWorld.setMachines(f.map(([uid,title,pos])=>({
  machine_uid:uid,title,status:'GREEN',pos,reason:'p11',
  sub_statuses:{qc:'GREEN',pm:'GREEN',calibration:'GREEN'},
  module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),fleet);
for(let k=0;k<150;k++){await page.evaluate(u=>window.__lemWorld.parse(u,'L'),fleet[k%7][0]);await page.waitForTimeout(90);}
await page.waitForTimeout(8000);
const f=await page.evaluate(()=>window.__gf);
console.log('fouls:',f.length);
for(const x of f) console.log(JSON.stringify(x));
console.log('ghost fouls:',f.filter(x=>x.ghost).length,'cross-line fouls:',f.filter(x=>x.cross).length);
await browser.close();
