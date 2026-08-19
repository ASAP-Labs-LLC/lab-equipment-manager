/* wshadow.mjs — for the dark near-field: is anything actually between that
 * ground and the sun? Raycasts screen points to the ground, then from the hit
 * point along the sun vector, and names the first thing it meets. */
import {chromium} from 'playwright';
const [,, url] = process.argv;
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(url + '&hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
const res = await p.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld, T = w.subsystems.get('terrain');
  const rc = new THREE.Raycaster();
  const all = []; w.scene.traverse(o => { if (o.isMesh && o.visible && o.castShadow) all.push(o); });
  const allAny = []; w.scene.traverse(o => { if (o.isMesh && o.visible) allAny.push(o); });
  let sun = null;
  w.scene.traverse(o => { if (o.isDirectionalLight && (!sun || o.intensity > sun.intensity)) sun = o; });
  const sd = sun ? sun.position.clone().sub(sun.target.position).normalize()
                 : new THREE.Vector3(0,1,0);
  const nm = o => o.name || o.parent?.name || o.material?.name || o.type;
  const out = [];
  for (const [sx, sy] of [[-0.6,-0.6],[-0.2,-0.7],[0.1,-0.5],[-0.6,-0.35],[0.3,-0.25]]) {
    rc.setFromCamera(new THREE.Vector2(sx, sy), w.camera);
    const g = rc.intersectObjects(allAny, false).filter(h => h.distance > 4)[0];
    if (!g) { out.push(`${sx},${sy} no ground`); continue; }
    const o = g.point.clone().addScaledVector(g.face ? g.face.normal : new THREE.Vector3(0,1,0), 0.05);
    rc.set(o, sd); rc.far = 900;
    const blockers = rc.intersectObjects(all, false).slice(0, 3)
      .map(h => `${nm(h.object)}@${h.distance.toFixed(1)}`);
    out.push(`${sx},${sy} ground=${nm(g.object)} y=${g.point.y.toFixed(1)} d=${g.distance.toFixed(0)}  sunward: ${blockers.join(', ') || 'CLEAR SKY'}`);
  }
  return {out, sunDir: sd.toArray().map(v=>v.toFixed(3)), sunI: sun?.intensity,
          casters: all.length,
          shadowCam: sun ? {l:sun.shadow.camera.left, r:sun.shadow.camera.right,
                            t:sun.shadow.camera.top, b:sun.shadow.camera.bottom,
                            n:sun.shadow.camera.near, f:sun.shadow.camera.far,
                            map:sun.shadow.mapSize.toArray()} : null};
});
console.log(res.out.join('\n'));
console.log('sunDir', res.sunDir, 'intensity', res.sunI, 'casters', res.casters);
console.log('shadowCam', JSON.stringify(res.shadowCam));
await b.close();
