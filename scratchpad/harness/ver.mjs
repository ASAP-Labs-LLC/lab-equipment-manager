import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:400,height:300}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=gi&hud=0', {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
const r = await p.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld;
  return {rev: THREE.REVISION,
          async: typeof w.engine.renderer.readRenderTargetPixelsAsync,
          sync: typeof w.engine.renderer.readRenderTargetPixels,
          copyFB: typeof w.engine.renderer.copyFramebufferToTexture};
});
console.log(JSON.stringify(r));
await b.close();
