import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
for (const v of process.argv.slice(4).map(Number)) {
  await p.evaluate(v => {
    const s = window.__lemWorld.ctx.sky; s._uniforms.uSkyCeil.value = v;
  }, v);
  await p.waitForTimeout(3000);
  await p.screenshot({path: `${process.argv[3]}-ceil${v}.png`});
}
await b.close();
