import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:960,height:540}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const sky = w.ctx?.sky || w.sky;
  const U = sky && sky._uniforms;
  const cam = w.engine.camera;
  const v = new (window.THREE ? window.THREE.Vector3 : Object)();
  return {
    weather: w.ctx ? {...w.ctx.weather} : w.weather,
    hours: sky?.hours,
    sunDir: sky ? sky.trueSunDirection.toArray().map(n=>+n.toFixed(3)) : null,
    sunAltDeg: sky ? +(Math.asin(sky.trueSunDirection.y)*57.2958).toFixed(2) : null,
    sunIntensity: sky?.sunIntensity, sunColour: sky?.sunColour?.toArray?.().map(n=>+n.toFixed(3)),
    ambient: sky?.ambientColour?.toArray?.().map(n=>+n.toFixed(4)),
    horizon: sky?.horizonColour?.toArray?.().map(n=>+n.toFixed(4)),
    fogColour: sky?.fogColour?.toArray?.().map(n=>+n.toFixed(4)),
    fogDensity: w.scene.fog?.density,
    uCloud: U?.uCloud.value, uSkyStop: U?.uSkyStop.value, uFogAmt: U?.uFogAmt.value,
    uSunLight: U?.uSunLight.value.toArray().map(n=>+n.toFixed(3)),
    uSkyLight: U?.uSkyLight.value.toArray().map(n=>+n.toFixed(3)),
    uCloudDensity: U?.uCloudDensity.value, uCloudBase: U?.uCloudBase.value,
    uSunDisc: U?.uSunDisc.value,
    camPos: cam.position.toArray().map(n=>+n.toFixed(1)),
    camDir: cam.getWorldDirection(new cam.position.constructor()).toArray().map(n=>+n.toFixed(3)),
  };
}), null, 1));
await b.close();
