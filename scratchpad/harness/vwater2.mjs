import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('console', m => { if (m.type()==='error') console.log('ERR', m.text().slice(0,200)); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation'), t = w.subsystems.get('terrain');
  const wy = t?.waterY;
  const base = (x,z) => t._baseHeight ? t._baseHeight(x,z) : NaN;
  const out = {waterY: wy, vegWaterLevel: v.waterLevel, plantFloor: v.plantFloor,
               range: v.range, quality: v.quality, tier: w.engine?.tier?.name};
  const tally = (name, xs, zs, n) => {
    let inWaterGraded = 0, inWaterBase = 0, minH = 1e9, minB = 1e9, far = 0;
    for (let i=0;i<n;i++) {
      const x = xs[i], z = zs[i];
      const h = t.heightAt(x,z), bh = base(x,z);
      if (h < minH) minH = h;
      if (bh < minB) minB = bh;
      if (h < wy) inWaterGraded++;
      if (bh < wy) inWaterBase++;
      const d = Math.hypot(x - (w.plan?.hub?.x||0), z - (w.plan?.hub?.z||0));
      if (d > far) far = d;
    }
    out[name] = {n, inWaterGraded, inWaterBase, minGraded:+minH.toFixed(1), minBase:+minB.toFixed(1), furthest:+far.toFixed(0)};
  };
  for (const e of v.trees) { /* accumulate later */ }
  // trees: concat
  let n=0; for (const e of v.trees) n += e.list.length;
  const xs = new Float64Array(n), zs = new Float64Array(n);
  let k=0; for (const e of v.trees) for (let i=0;i<e.list.length;i++){ xs[k]=e.xs[i]; zs[k]=e.zs[i]; k++; }
  tally('trees', xs, zs, n);
  let gn=0; for (const g of v.groves) gn += g.count;
  const gx = new Float64Array(gn), gz = new Float64Array(gn);
  k=0; for (const g of v.groves) for (let i=0;i<g.count;i++){ gx[k]=g.xs[i]; gz[k]=g.zs[i]; k++; }
  tally('groves', gx, gz, gn);
  let cn=0; for (const c of v.clutter) cn += c.count;
  const cx = new Float64Array(cn), cz = new Float64Array(cn);
  k=0; for (const c of v.clutter) for (let i=0;i<c.count;i++){ cx[k]=c.xs[i]; cz[k]=c.zs[i]; k++; }
  tally('clutter', cx, cz, cn);
  const G = v.grass;
  const grx = new Float64Array(G.count), grz = new Float64Array(G.count);
  for (let i=0;i<G.count;i++){ grx[i]=G.mats[i*16+12]; grz[i]=G.mats[i*16+14]; }
  tally('grass', grx, grz, G.count);
  // how much of the scatter disc is water by each measure
  let wg=0, wb=0, tot=0;
  for (let j=0;j<120;j++) for (let i=0;i<120;i++) {
    const x = -3000 + i*50, z = -3000 + j*50;
    tot++; if (t.heightAt(x,z) < wy) wg++; if (base(x,z) < wy) wb++;
  }
  out.discWaterFracGraded = +(wg/tot).toFixed(4);
  out.discWaterFracBase = +(wb/tot).toFixed(4);
  out.stats = w.stats?.();
  return out;
});
console.log(JSON.stringify(r,null,1));
await b.close();
