/* Inventory of what is and is not in the shadow map, plus the sun fit. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text().slice(0,300)); });
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(4000);
const info = await p.evaluate(() => {
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  const cam = w.camera;
  const rows = [];
  const THREE = window.THREE_NS;
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    const g = o.geometry;
    const r = g?.boundingSphere?.radius ?? null;
    const wp = o.getWorldPosition(new (o.position.constructor)());
    rows.push({
      n: o.name || o.type,
      inst: o.isInstancedMesh ? (o.count|0) : 0,
      cast: !!o.castShadow, recv: !!o.receiveShadow,
      r: r === null ? null : +(r.toFixed(1)),
      d: +wp.distanceTo(cam.position).toFixed(0),
      mat: (Array.isArray(o.material)?o.material[0]:o.material)?.type,
      tri: (g?.index ? g.index.count/3 : (g?.attributes?.position?.count||0)/3)|0,
    });
  });
  const s = gi?.sun;
  const sc = s?.shadow?.camera;
  return {
    fit: gi?._shadowFit ? {c: gi._shadowFit.centre.toArray().map(v=>+v.toFixed(1)), r: gi._shadowFit.radius} : null,
    sunPos: s?.position?.toArray().map(v=>+v.toFixed(1)),
    tgt: s?.target?.position?.toArray().map(v=>+v.toFixed(1)),
    mapSize: s?.shadow?.mapSize?.toArray(),
    ortho: sc ? {l: sc.left, r: sc.right, n: +sc.near.toFixed(1), f: +sc.far.toFixed(1)} : null,
    nbias: s?.shadow?.normalBias, bias: s?.shadow?.bias,
    intensity: s?.intensity, cast: s?.castShadow,
    exposure: gi?.exposure, gi: gi?.uniforms?.lemGIStrength?.value,
    fillE: gi?._fillE, keyE: gi?._keyE, sceneIrr: gi?.sceneIrradiance,
    camPos: cam.position.toArray().map(v=>+v.toFixed(0)),
    rows: rows.filter(x => !x.cast),
    castCount: rows.filter(x => x.cast).length,
    total: rows.length,
  };
});
console.log(JSON.stringify(info, null, 1));
await b.close();
