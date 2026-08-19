import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
p.on('console',m=>{const t=m.text(); if(/veget|grove/i.test(t)) console.log('['+m.type()+']',t.slice(0,300));});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,300)));
const url=process.argv[2]||'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=wide&time=16&hud=0';
await p.goto(url,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{const v=window.__lemWorld.subsystems.get('vegetation');
  return {groves:(v.groves||[]).length, groveR:v.groveR, cells:v._standCells?v._standCells.size:null,
          matGrove:!!v.matGrove, trees:(v.trees||[]).reduce((a,e)=>a+e.list.length,0)};})));
await b.close();
