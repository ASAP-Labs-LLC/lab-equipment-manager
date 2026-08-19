/* lblquad3.mjs — what exactly is the black plate on multitek-s? Pull the face
 * normal, the local-space box of the connected triangles, and the actual texel
 * the uv lands on, so the cause is named rather than guessed. */
import {chromium} from 'playwright';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings&cam=yard&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

const out = await page.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld;
  const cam = w.camera || w.ctx?.camera, scene = w.scene || w.ctx?.scene;
  const st = w.plan.byUid.get('multitek-s');

  let mesh = null;
  scene.traverse(o => { if (o.name === 'multitek-s:rust') mesh = o; });
  if (!mesh) return {err: 'no mesh'};

  const rc = new THREE.Raycaster();
  rc.setFromCamera(new THREE.Vector2((480 / 1280) * 2 - 1, -((205 / 720) * 2 - 1)), cam);
  const h = rc.intersectObject(mesh, true)[0];

  const g = mesh.geometry;
  const pos = g.attributes.position, uv = g.attributes.uv;
  const idx = g.index;
  const face = h.faceIndex;
  const tri = i => (idx ? [idx.getX(i * 3), idx.getX(i * 3 + 1), idx.getX(i * 3 + 2)]
                        : [i * 3, i * 3 + 1, i * 3 + 2]);

  /* Grow from the hit face over vertices it shares, to get the whole plate. */
  const want = new Set(tri(face));
  const faces = new Set([face]);
  const nFaces = (idx ? idx.count : pos.count) / 3;
  for (let pass = 0; pass < 4; pass++) {
    for (let f = 0; f < nFaces; f++) {
      if (faces.has(f)) continue;
      const t = tri(f);
      if (t.some(v => want.has(v))) { faces.add(f); t.forEach(v => want.add(v)); }
    }
  }
  const box = new THREE.Box3();
  const v3 = new THREE.Vector3();
  const uvs = [];
  for (const v of want) {
    box.expandByPoint(v3.fromBufferAttribute(pos, v).clone());
    if (uv) uvs.push([+uv.getX(v).toFixed(2), +uv.getY(v).toFixed(2)]);
  }

  /* The texel the hit uv actually samples. */
  let texel = null, texMeta = null;
  const map = mesh.material.map;
  if (map && map.image) {
    const im = map.image;
    texMeta = {w: im.width, h: im.height, kind: im.constructor.name,
               wrapS: map.wrapS, wrapT: map.wrapT, cs: map.colorSpace,
               repeat: [map.repeat.x, map.repeat.y]};
    try {
      const c = document.createElement('canvas');
      c.width = im.width; c.height = im.height;
      c.getContext('2d').drawImage(im, 0, 0);
      const d = c.getContext('2d').getImageData(0, 0, im.width, im.height).data;
      const sample = (u, v) => {
        const x = Math.floor(((u % 1) + 1) % 1 * im.width);
        const y = Math.floor((1 - (((v % 1) + 1) % 1)) * im.height);
        const i = (y * im.width + x) * 4;
        return [d[i], d[i + 1], d[i + 2], d[i + 3]];
      };
      texel = {atHit: sample(h.uv.x, h.uv.y),
               corners: [sample(0.1, 0.1), sample(0.5, 0.5), sample(0.9, 0.9)]};
    } catch (e) { texel = {err: String(e).slice(0, 80)}; }
  }

  return {
    station: st ? {x: st.x, z: st.z, title: st.title, machine: st.machine?.status} : null,
    meshPos: mesh.position.toArray(), meshScale: mesh.scale.toArray(),
    hit: {d: +h.distance.toFixed(1), uv: [h.uv.x, h.uv.y], face,
          normal: h.face ? [h.face.normal.x, h.face.normal.y, h.face.normal.z] : null,
          pt: h.point.toArray().map(n => +n.toFixed(2))},
    plate: {faces: faces.size, verts: want.size,
            min: box.min.toArray().map(n => +n.toFixed(2)),
            max: box.max.toArray().map(n => +n.toFixed(2)),
            size: box.getSize(new THREE.Vector3()).toArray().map(n => +n.toFixed(2)),
            uvs: uvs.slice(0, 24)},
    material: {name: mesh.material.name, colour: '#' + mesh.material.color.getHexString(),
               rough: mesh.material.roughness, metal: mesh.material.metalness,
               emissive: '#' + mesh.material.emissive?.getHexString(),
               side: mesh.material.side, transparent: mesh.material.transparent,
               aoMap: !!mesh.material.aoMap, normalMap: !!mesh.material.normalMap,
               vertexColors: mesh.material.vertexColors},
    texMeta, texel,
    geomAttrs: Object.keys(g.attributes), triangles: nFaces,
  };
});

console.log(JSON.stringify(out, null, 2));
await browser.close();
