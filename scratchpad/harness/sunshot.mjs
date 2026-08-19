/* Two rounds of critics called the sun disc invisible. None of solo.html's
 * camera presets is guaranteed to have the sun in shot, so this one turns the
 * rig to face it before the screenshot. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], out = process.argv[3], pitch = parseFloat(process.argv[4] || '0.10');
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
const errs=[]; p.on('pageerror', e=>errs.push(String(e))); p.on('console', m=>{if(m.type()==='error')errs.push(m.text());});
await p.goto(url, {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.evaluate((pitch) => {
  const w = window.__lemWorld, sky = w.ctx?.sky || w.sky;
  const d = sky.trueSunDirection;
  w.rig.goalYaw = Math.atan2(-d.x, -d.z);
  w.rig.goalPitch = pitch;
  w.rig.idleDrift = false;
  w.rig.apply(1);
}, pitch);
await p.waitForTimeout(3500);
await p.screenshot({path: out});
console.log(JSON.stringify({out, errs, dc: await p.evaluate(()=>window.__lemWorld.engine.drawCalls),
  tri: await p.evaluate(()=>window.__lemWorld.engine.triangles)}));
await b.close();
