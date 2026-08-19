import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(3500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, s = w.scene, out = {};
  out.fog = s.fog ? {type: s.fog.type, color: '#' + s.fog.color.getHexString(),
                     near: s.fog.near, far: s.fog.far, density: s.fog.density} : null;
  out.background = s.background && s.background.isColor ? '#' + s.background.getHexString()
                 : (s.background ? s.background.type || 'texture' : null);
  out.envIntensity = s.environmentIntensity;
  const L = [];
  s.traverse(o => {
    if (o.isDirectionalLight && o.intensity > 0.01)
      L.push({kind:'sun', colour:'#'+o.color.getHexString(), intensity:+o.intensity.toFixed(2)});
    if (o.isHemisphereLight)
      L.push({kind:'hemi', sky:'#'+o.color.getHexString(), ground:'#'+o.groundColor.getHexString(),
              intensity:+o.intensity.toFixed(2)});
    if (o.isAmbientLight)
      L.push({kind:'ambient', colour:'#'+o.color.getHexString(), intensity:+o.intensity.toFixed(2)});
  });
  out.lights = L;
  const gi = w.subsystems.get('gi');
  if (gi) out.gi = {sunColour: gi.sunColour ? '#'+gi.sunColour.getHexString() : null,
                    exposure: gi.exposure, sceneIrradiance: gi.sceneIrradiance};
  // What colour is the foliage actually rendering?
  const veg = w.subsystems.get('vegetation');
  out.vegMaterials = [];
  s.traverse(o => {
    if (o.isMesh && o.material && /leaf|foliage|canopy|needle/i.test(o.material.name || o.name || '')) {
      out.vegMaterials.push({name: o.material.name || o.name,
                             colour: o.material.color ? '#'+o.material.color.getHexString() : null});
    }
  });
  void veg;
  return out;
}), null, 1));
await b.close();
