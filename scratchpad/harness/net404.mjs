import {chromium} from 'playwright';
const b=await chromium.launch({args:['--use-angle=metal']});const p=await b.newPage();
p.on('response',r=>{if(r.status()>=400)console.log(r.status(), r.url());});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(3000);
await b.close();
