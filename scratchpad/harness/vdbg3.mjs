import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERR', String(e).slice(0,300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  const Tex=v.ctx.Tex, bb=v._area(v.plan);
  const noise=(x,z,s,sc)=>Tex.fbm(x*sc,z*sc,{octaves:3,period:8,seed:s});
  const sm=(a,b,x)=>{const t=Math.max(0,Math.min(1,(x-a)/(b-a)));return t*t*(3-2*t);};
  const R={stand:0,site:0,open:0,dice:0,weight:0,alt:0,slope:0,pass:0};
  let seed=12345; const rnd=()=>{seed=(seed*1664525+1013904223)>>>0; return seed/4294967296;};
  const step=7;
  const cols=Math.floor((bb.x1-bb.x0)/step), rows=Math.floor((bb.z1-bb.z0)/step);
  const SP=[[1.0,-0.2,1.2,1.0],[0.68,0.05,1.05,1.2],[0.9,-0.2,1.06,0.85],[0.95,-0.3,0.84,0.7],[0.85,-0.3,1.06,0.85]];
  const altS=[];
  for(let j=0;j<rows;j+=3)for(let i=0;i<cols;i+=3){
    const x=bb.x0+(i+rnd())*step, z=bb.z0+(j+rnd())*step;
    const stand=noise(x,z,7,0.0018), grain=noise(x,z,23,0.011);
    let d=sm(0.14,0.34,stand)*(0.72+0.55*grain);
    if(d<=0.02){R.stand++;continue;}
    const site=v._site(x,z); if(!site){R.site++;continue;}
    d*=v._openness(x,z); if(d<=0.02){R.open++;continue;}
    d*=1-sm(0.45,0.95,site.slope);
    if(v.relief>25) d*=1-sm(0.70,0.94,site.alt);
    if(d<=0.02||rnd()>d){R.dice++;continue;}
    const mix=noise(x,z,61,0.0032);
    const conifer=Math.max(0,Math.min(1,0.02+site.alt*0.52+site.slope*0.7+(mix-0.5)*1.1));
    let si; if(rnd()<conifer) si=rnd()<0.66?0:1; else si=[2,3,4][Math.floor(Math.max(0,Math.min(2.99,mix*2.4+rnd()*0.8)))];
    const sp=SP[si];
    if(rnd()>sp[0]){R.weight++;continue;}
    if(site.alt<sp[1]||site.alt>sp[2]){R.alt++;altS.push(+site.alt.toFixed(2));continue;}
    if(site.slope>sp[3]*1.2){R.slope++;continue;}
    R.pass++;
  }
  const sSample=[]; for(let k=0;k<12;k++){const x=bb.x0+Math.random()*(bb.x1-bb.x0),z=bb.z0+Math.random()*(bb.z1-bb.z0);const s=v._biome(x,z,v._ground(x,z));sSample.push({alt:+s.alt.toFixed(2),slope:+s.slope.toFixed(2),wet:+s.wet.toFixed(2)});}
  return {R, sSample, hasBiomeAt: typeof v._terrain?.biomeAt};
}),null,1));
await b.close();
