/* A picture of a working actually on the road, rather than of a yard full of
 * standing trains — the interlocking is only interesting while something moves. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const FLEET = [['multitek-ns','Multitek NS','GREEN'],['multitek-s','Multitek S','YELLOW'],
  ['optimpp-1','OptiMPP 1','GREEN'],['optimpp-2','OptiMPP 2','RED'],
  ['pac-flash-1','PAC Flash 1','SERVICE'],['pac-flash-2','PAC Flash 2','DEAD-LINE'],
  ['koehler-cp','Koehler CP','UNKNOWN']];
const POS=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
const b = await chromium.launch({headless:true,args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam='+(process.argv[2]||'wide')+'&time=16&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map(([uid,title,status],i)=>({
  machine_uid:uid,title,status,pos:pos[i],reason:'shot',
  sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},module_running:true,
  module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,POS]);
await p.waitForTimeout(2500);
for (const f of FLEET) { await p.evaluate(u=>window.__lemWorld.parse(u,'L'),f[0]); }
await p.waitForTimeout(parseInt(process.argv[3]||'14000',10));
const st = await p.evaluate(()=>{const T=window.__lemWorld.subsystems.get('trains');
  return T.consists.filter(c=>c.state!=='idle').map(c=>({slot:c.slot,st:c.state,line:c.line,
    s:+c.s.toFixed(0),v:+c.v.toFixed(1),holds:[...(c.holds||[])]}));});
console.log(JSON.stringify(st));
fs.writeFileSync(process.argv[4]||'/tmp/running.png', await p.screenshot());
await b.close();
