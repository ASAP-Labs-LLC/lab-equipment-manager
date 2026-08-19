import {chromium} from 'playwright';
const FLEET=[['multitek-ns','Multitek NS'],['multitek-s','Multitek S'],['optimpp-1','OptiMPP 1'],['optimpp-2','OptiMPP 2'],['pac-flash-1','PAC Flash 1'],['pac-flash-2','PAC Flash 2'],['koehler-cp','Koehler CP']];
const BAY=2.05; let seed=12345; const rnd=()=>(seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
const all=[[0,0]]; // layout 3 = kind 3 -> need to replay rng
const layouts=[]; { let s2=12345; const r=()=>(s2=(s2*1103515245+12345)&0x7fffffff)/0x7fffffff;
 for(let L=1;L<4;L++){const kind=L%4;const pos=[];for(let i=0;i<7;i++){ if(kind===0)pos.push([Math.round(r()*8)*BAY,Math.round(r()*8)*BAY]); else if(kind===1)pos.push([i*BAY,0]); else if(kind===2)pos.push([0,i*BAY]); else pos.push([Math.round(r()*14)*BAY,Math.round(r()*14)*BAY]);} if(kind===3)pos[1]=pos[0].slice(); layouts.push(pos);} }
const pos=layouts[2];
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal']});
const p=await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail&cam=yard&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map(([uid,title],i)=>({machine_uid:uid,title,status:'GREEN',pos:pp[i],sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,pos]);
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const rail=window.__lemWorld.subsystems.get('rail');
  const out=[];
  for(const st of window.__lemWorld.plan.stations){
    const r=rail.route(st.uid); if(!r)continue;
    let worst=0,at=0;
    for(let i=1;i<r.points.length;i++){const d=r.points[i].distanceTo(r.points[i-1]); if(d>worst){worst=d;at=i;}}
    if(worst>30){
      const sd=rail.sidings.get(st.uid);
      const br=rail.branchOf.get(sd.line);
      out.push({uid:st.uid,worst:+worst.toFixed(1),at,
        a:r.points[at-1].toArray().map(v=>+v.toFixed(1)),
        b:r.points[at].toArray().map(v=>+v.toFixed(1)),
        sDock:+sd.sDock.toFixed(1),exitS:+sd.exitS.toFixed(1),entryS:+sd.entryS.toFixed(1),
        sIn:+sd.sIn.toFixed(1),sOut:+sd.sOut.toFixed(1),roadLen:+sd.track.length.toFixed(1),
        roadName:sd.track.name, lineName:sd.line.name, lineLen:+sd.line.length.toFixed(1),
        jS:br?+br.jS.toFixed(1):null, tS:br?+br.tS.toFixed(1):null,
        renderFrom:+sd.line.renderFrom.toFixed(1), renderTo:+sd.line.renderTo.toFixed(1)});
    }
  }
  return out;
}),null,1));
await b.close();
