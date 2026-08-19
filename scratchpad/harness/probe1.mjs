import {chromium} from 'playwright';
const browser = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport:{width:1280,height:720}});
page.on('pageerror', e=>console.log('PE', String(e).slice(0,200)));
await page.goto(process.argv[2], {waitUntil:'load', timeout:60000});
await page.waitForFunction(()=>window.__worldReady===true, null, {timeout:45000});
await page.waitForTimeout(1000);
console.log(JSON.stringify(await page.evaluate(()=>{
  const w=window.__lemWorld, tr=w.subsystems.get('trains'), rail=w.subsystems.get('rail');
  const tri = g => g && g.index ? g.index.count/3 : (g?.attributes?.position?.count/3||0);
  const out={tank: tri(tr.tankGeo.geo), gp: tri(tr.locoGeo.gp.geo), sd: tri(tr.locoGeo.sd.geo)};
  out.consists = tr.consists.map(c=>({n:c.vehicles.length, len:Math.round(c.length),
     tris: c.vehicles.reduce((a,v)=>a+tri(v.mesh.geometry),0)}));
  out.trucks = (tr.truckMeshes||[]).map(m=>({count:m.count, tri:tri(m.geometry), total:m.count*tri(m.geometry)}));
  out.wheels = Object.entries(tr.wheelMeshes||{}).map(([k,v])=>({k,count:v.mesh.count,tri:tri(v.mesh.geometry),total:v.mesh.count*tri(v.mesh.geometry)}));
  out.railTracks = (rail?.tracks||[]).map(t=>({n:t.name,len:Math.round(t.length)}));
  out.railTotalLen = Math.round((rail?.tracks||[]).reduce((a,t)=>a+t.length,0));
  out.railMeshes = (rail?._meshes||[]).map(m=>({inst:m.count||1, tri:tri(m.geometry), total:(m.count||1)*tri(m.geometry)}));
  out.railTris = out.railMeshes.reduce((a,m)=>a+m.total,0);
  return out;
}), null, 1));
await browser.close();
