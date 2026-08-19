import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal']});
const p=await b.newPage({viewport:{width:800,height:450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&time=16',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
console.log(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const NB=36, out=new Array(NB).fill(0);
  const seg=(ax,az,bx,bz,pad)=>{
    for(let s=0;s<=40;s++){const u=s/40;const x=ax+(bx-ax)*u,z=az+(bz-az)*u;
      const r=Math.hypot(x-t.cx,z-t.cz)+pad;
      const a=Math.atan2(z-t.cz,x-t.cx);
      // spread over the angular half-width the pad subtends
      const rr=Math.max(1,Math.hypot(x-t.cx,z-t.cz));
      const half=Math.atan2(pad,rr);
      for(let k=-3;k<=3;k++){
        const aa=a+half*k/3;
        let i=Math.round((aa/(Math.PI*2)+1)*NB)%NB;
        out[i]=Math.max(out[i], r);
      }}};
  for(const f of t.features||[]){
    const pad=(f.t===0?Math.max(f.hx,f.hz):f.r)||0;
    if(f.t===0){ // rect pad: walk its outline
      for(const [dx,dz] of [[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]].slice(0,4).map((c,i,arr)=>c)) {}
      const c=[[-f.hx,-f.hz],[f.hx,-f.hz],[f.hx,f.hz],[-f.hx,f.hz]];
      for(let i=0;i<4;i++){const a=c[i],d=c[(i+1)%4];seg(f.cx+a[0],f.cz+a[1],f.cx+d[0],f.cz+d[1],6);}
    } else seg(f.ax,f.az,f.bx,f.bz,pad);
  }
  return JSON.stringify(out.map(v=>+v.toFixed(0)));
}));
await b.close();
