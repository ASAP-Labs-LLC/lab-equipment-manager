/* Does the toggle actually STOP the renderer, and do picks still work? */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1600,height:950}, deviceScaleFactor:1})).newPage();
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,160)));
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000});
await p.waitForTimeout(14000);

const frames = async label => {
  const a = await p.evaluate(()=>window.__lemWorld.engine.frame ?? 0);
  await p.waitForTimeout(2000);
  const bb = await p.evaluate(()=>window.__lemWorld.engine.frame ?? 0);
  console.log(`${label.padEnd(22)} frames advanced in 2s: ${bb-a}`);
  return bb-a;
};
await frames('site view');
await p.click('#btnView'); await p.waitForTimeout(1200);
console.log('button now reads   :', await p.evaluate(()=>document.getElementById('btnView').textContent.trim()));
console.log('canvas hidden      :', await p.evaluate(()=>document.getElementById('world').hidden));
console.log('plan visible       :', await p.evaluate(()=>getComputedStyle(document.getElementById('floorSimple')).display !== 'none'));
console.log('machines drawn     :', await p.evaluate(()=>document.querySelectorAll('#floorSimple .simple-machine').length));
await frames('plan view');
await p.screenshot({path:'/tmp/floor-plan.png'});
// a pick in plan view must open the rail.
// NOT `!#railL .empty`: an OPEN record contains several `.empty` placeholders
// of its own ("No QC results recorded yet", "Loading…"), so that test reads
// false for every pick, in either view. The record's heading is the honest
// signal — and it has to be the instrument that was actually clicked.
const want = await p.evaluate(()=>document.querySelector('#floorSimple .simple-machine')
  .getAttribute('aria-label').split(',')[0]);
await p.click('#floorSimple .simple-machine .hit');
await p.waitForTimeout(900);
const head = await p.evaluate(()=>document.querySelector('#railL h2')?.textContent?.trim() || '');
console.log(`pick opened a rail : ${head === want ? 'yes' : 'NO'} (wanted ${want}, rail says ${head || '—'})`);
await p.click('#btnView'); await p.waitForTimeout(1500);
await frames('back to site');
console.log(errs.length ? 'ERRORS: '+errs.slice(0,3).join(' | ') : 'no page errors');
await b.close();
