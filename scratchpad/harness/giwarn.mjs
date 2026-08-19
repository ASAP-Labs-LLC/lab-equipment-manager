/* giwarn.mjs — collect every console message gi.js emits.
 * `after()` warns once per anchor when a shader splice misses, and a missed
 * splice is silent in every other harness this project has: the material still
 * compiles and the term is simply never applied. Round 8's notes are explicit
 * that this cost weeks once. New anchors get checked here before they ship. */
import {chromium} from 'playwright';
const MODS='sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const q = process.argv[2] || 'floor';
const url=`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=yard&time=16&weather=clear&hud=0&quality=${q}`;
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage();
const msgs=[];
p.on('console',m=>{ const t=m.text(); if(/\[gi\]/.test(t)) msgs.push(m.type()+': '+t.slice(0,180)); });
p.on('pageerror',e=>msgs.push('pageerror: '+String(e).slice(0,180)));
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(6000);
/* Step the ladder both ways, so every define combination this file can compile
 * is actually compiled before the messages are read. */
for (const t of ['ultra','floor','medium','floor','high']) {
  await p.evaluate(n=>window.__lemWorld.engine.setQualityMode(n), t);
  await p.waitForTimeout(2500);
}
console.log(JSON.stringify(msgs,null,1));
await b.close();
