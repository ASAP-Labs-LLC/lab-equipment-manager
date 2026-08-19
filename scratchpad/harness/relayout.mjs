import {chromium} from 'playwright';
const FLEET = [['multitek-ns','Multitek NS'],['multitek-s','Multitek S'],['optimpp-1','OptiMPP 1'],
               ['optimpp-2','OptiMPP 2'],['pac-flash-1','PAC Flash 1'],['pac-flash-2','PAC Flash 2'],
               ['koehler-cp','Koehler CP']];
const b = await chromium.launch({headless:true, channel:'chromium'});
for (const doRelayout of [false, true]) {
  const p = await b.newPage({viewport:{width:900,height:560}});
  await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=yard&time=15&hud=0',{waitUntil:'load'});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
  await p.waitForTimeout(2500);
  if (doRelayout) {
    await p.evaluate(fleet => {
      const pos = [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
      window.__lemWorld.setMachines(fleet.map(([uid,title],i)=>({
        machine_uid:uid, title, status:'GREEN', pos:pos[i], reason:'t',
        sub_statuses:{qc:'GREEN',pm:'GREEN',calibration:'GREEN'},
        module_running:true, module_state:'running',
        effective_specs:[], qc_targets:[], maintenance:[]})));
    }, FLEET);
    await p.waitForTimeout(3000);
  }
  await p.evaluate(() => {
    window.__t = []; window.__s = {};
    const T = window.__lemWorld.subsystems.get('trains'); const prev = new Map();
    const tick = () => { for (const c of T.consists) { if(!c) continue;
      window.__s[c.state]=(window.__s[c.state]||0)+1;
      const w=prev.get(c.slot); if(w!==undefined&&w!==c.state&&window.__t.length<20) window.__t.push(`${w}->${c.state}`);
      prev.set(c.slot,c.state);} requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    for (let i=0;i<10;i++) setTimeout(()=>window.__lemWorld.parse(['multitek-ns','optimpp-1','pac-flash-1'][i%3],'L'), i*200);
  });
  await p.waitForTimeout(40000);
  const r = await p.evaluate(()=>({s:window.__s, t:window.__t}));
  console.log(`relayout=${doRelayout}  states=${JSON.stringify(r.s)}  transitions=${JSON.stringify(r.t.slice(0,8))}`);
  await p.close();
}
await b.close();
