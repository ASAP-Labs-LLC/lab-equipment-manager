/* zz-terminal.mjs — does the plan give a train anywhere to stand clear?
 * Fires parses at every bench and measures queueing at the single rack stand
 * and on the shared main. Read-only. */
import {chromium} from 'playwright';
const UIDS=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail,trains&time=13&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
await p.evaluate(()=>{
  const w=window.__lemWorld, tr=w.subsystems.get('trains'), rail=w.subsystems.get('rail');
  window.__m={waitFrames:0, frames:0, maxOut:0, maxOnMain:0, atRack:{}, states:{},
              longestWait:0, waitBy:{}, arrivals:0, onMainHist:{}};
  const t0=performance.now();
  const waitStart=new Map();
  setInterval(()=>{
    const m=window.__m; m.frames++;
    let out=0, onMain=0;
    for(const c of tr.consists){
      if(c.shunt) continue;
      m.states[c.state]=(m.states[c.state]||0)+1;
      if(c.state!=='idle'&&c.state!=='loading') out++;
      const cyc=rail.cycle(c.uid);
      if(cyc&&c.state!=='idle'){
        // is the head on the shared trunk?
        for(const g of cyc.segments){
          if(g.track!=='main') continue;
          const a=cyc.route.acc[g.from], bb=cyc.route.acc[g.to];
          if(c.s>=a&&c.s<=bb) onMain++;
        }
      }
      if(c.waiting){ m.waitFrames++; m.waitBy[c.uid]=(m.waitBy[c.uid]||0)+1;
        if(!waitStart.has(c.slot)) waitStart.set(c.slot, performance.now());
        m.longestWait=Math.max(m.longestWait,(performance.now()-waitStart.get(c.slot))/1000);
      } else waitStart.delete(c.slot);
    }
    m.maxOut=Math.max(m.maxOut,out); m.maxOnMain=Math.max(m.maxOnMain,onMain);
    m.onMainHist[onMain]=(m.onMainHist[onMain]||0)+1;
  },100);
});
for (let r=0;r<3;r++){
  for (const u of UIDS){ await p.evaluate(u=>window.__lemWorld.parse(u,'AUDIT'),u); await p.waitForTimeout(150); }
  await p.waitForTimeout(20000);
}
await p.waitForTimeout(25000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, tr=w.subsystems.get('trains');
  return {...window.__m, stats:w.stats(),
    now: tr.consists.filter(c=>!c.shunt).map(c=>`${c.uid}:${c.state}${c.waiting?'/WAIT':''}@${Math.round(c.s)}`)};
}),null,1));
await b.close();
