import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium', args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 900, height: 600}});
const msgs = [];
p.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') msgs.push(m.type() + ': ' + m.text().slice(0, 200)); });
p.on('pageerror', e => msgs.push('pageerror: ' + String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const SEQ = [[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
             [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]],
             [[0,0],[0,2.05],[0,4.1],[0,6.15],[0,8.2],[0,10.25],[0,12.3]],
             [[0,0],[0,0],[8.2,4.1],[12.3,8.2],[2.05,10.25],[16.4,2.05],[6.15,14.35]]];
for (const pos of SEQ) {
  await p.evaluate(([f, pp]) => window.__lemWorld.setMachines(f.map((uid, i) => ({
    machine_uid: uid, title: uid, status: 'GREEN', pos: pp[i], reason: 'warn',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: []}))), [F, pos]);
  await p.waitForTimeout(2500);
  for (let i = 0; i < 40; i++) { await p.evaluate(u => window.__lemWorld.parse(u, 'L'), F[i % 7]); await p.waitForTimeout(120); }
  await p.waitForTimeout(6000);
}
console.log(msgs.length ? msgs.slice(0, 15).join('\n') : 'no warnings or errors');
await b.close();
