/* whit.mjs — raycast a grid of screen points and report which mesh answers.
 * "The water is the worst thing in the frame" is only actionable if the pixels
 * being complained about are actually the water. */
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
  const all = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible) all.push(o); });
  const rows = [];
  for (let sy = -0.15; sy >= -0.95; sy -= 0.16) {
    const row = [];
    for (let sx = -0.8; sx <= 0.81; sx += 0.4) {
      rc.setFromCamera(new THREE.Vector2(sx, sy), w.camera);
      const hits = rc.intersectObjects(all, false).filter(h => h.distance > 4);
      const h = hits[0];
      const nm = o => o.name || o.parent?.name || o.material?.name || o.type;
      row.push(h ? `${nm(h.object)}@${h.distance.toFixed(0)}` : '-');
    }
    rows.push(`y=${sy.toFixed(2)} ` + row.join(' | '));
  }
  return {rows, waterY: T.waterY, cam: w.camera.position.toArray().map(v=>v.toFixed(1)),
          groundAtCam: T.heightAt(w.camera.position.x, w.camera.position.z).toFixed(2)};
});
console.log(res.rows.join('\n'));
console.log('waterY', res.waterY, 'cam', res.cam, 'ground@cam', res.groundAtCam);
await b.close();
