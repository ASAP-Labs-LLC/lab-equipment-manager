import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = n => rail.tracks.find(t => t.name === n);
  const link = rail.tracks.find(t => t.name.startsWith('link:'));
  if (!link) return 'no link';
  const rec = rail._turnouts.filter(r => r.child === link);
  const v = q => [+q.x.toFixed(2), +q.y.toFixed(2), +q.z.toFixed(2)];
  const out = {link: link.name, len: +link.length.toFixed(2),
               hasDesignY: !!link.designY, tight: link.tight,
               maxGrade: link.maxGrade, ruling: link.ruling,
               overGrade: link.overGrade,
               joints: rec.map(r => ({
                 parent: r.track.name, s: +r.s.toFixed(2), which: r.which,
                 drop: +(r.drop || 0).toFixed(3),
                 parentPos: v(r.track.at(r.s).position),
                 childPos: v(link.at(r.which === 'start' ? 0 : link.length).position),
               })),
               profile: []};
  for (let s = 0; s <= link.length; s += 5) {
    out.profile.push([+s.toFixed(1), +link.at(s).position.y.toFixed(2)]);
  }
  const road = T('load:0');
  out.roadY = +road.at(223.4).position.y.toFixed(2);
  out.lineY = +T('branch0').at(406.5).position.y.toFixed(2);
  out.groundAtLink = (() => {
    const g = window.__lemWorld.ctx?.ground;
    const q = link.at(link.length / 2).position;
    try { return +g.height(q.x, q.z).toFixed(2); } catch (e) { return String(e).slice(0,60); }
  })();
  return out;
}), null, 1));
await b.close();
