import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:900,height:500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&cam=wide&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain');
  const out={waterY:t.waterY, meshes:[]};
  t.group.traverse(o=>{
    if(!o.isMesh||!o.geometry?.attributes?.aux) return;
    const g=o.geometry, pos=g.attributes.position, aux=g.attributes.aux;
    const sa=g.attributes.splatA, sb=g.attributes.splatB;
    const bins={};
    const n=pos.count;
    let lowN=0, lowAuxW=0, lowSA=[0,0,0,0], lowSB=[0,0,0,0], lowNy=0;
    const nor=g.attributes.normal;
    for(let i=0;i<n;i++){
      const y=pos.getY(i)+o.position.y;
      const aw=y-t.waterY;
      const k=aw<0?'sub':aw<1?'0-1':aw<2?'1-2':aw<3?'2-3':aw<5?'3-5':aw<10?'5-10':'10+';
      if(!bins[k]) bins[k]={n:0,auxW:0,auxY:0,ny:0};
      bins[k].n++; bins[k].auxW+=aux.getW(i); bins[k].auxY+=aux.getY(i);
      if(nor) bins[k].ny+=nor.getY(i);
      if(aw>=0&&aw<2){lowN++; lowAuxW+=aux.getW(i);
        if(sa){lowSA[0]+=sa.getX(i);lowSA[1]+=sa.getY(i);lowSA[2]+=sa.getZ(i);lowSA[3]+=sa.getW(i);}
        if(sb){lowSB[0]+=sb.getX(i);lowSB[1]+=sb.getY(i);lowSB[2]+=sb.getZ(i);lowSB[3]+=sb.getW(i);}
        if(nor) lowNy+=nor.getY(i);}
    }
    for(const k in bins){bins[k].auxW=+(bins[k].auxW/bins[k].n).toFixed(3); bins[k].auxY=+(bins[k].auxY/bins[k].n).toFixed(3); bins[k].ny=+(bins[k].ny/bins[k].n).toFixed(3);}
    out.meshes.push({name:o.name||'(anon)', verts:n, groupY:+o.position.y.toFixed(2), bins,
      lowN, lowAuxW:+(lowAuxW/Math.max(1,lowN)).toFixed(3),
      lowNy:+(lowNy/Math.max(1,lowN)).toFixed(3),
      lowSplatA:lowSA.map(v=>+(v/Math.max(1,lowN)).toFixed(3)),
      lowSplatB:lowSB.map(v=>+(v/Math.max(1,lowN)).toFixed(3))});
  });
  return out;
}),null,1));
await b.close();
