import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const secKeys = [...(rail._sections?.keys() || [])];
  const t0 = rail.tracks?.[0];
  return {
    tracksIsArray: Array.isArray(rail.tracks),
    nTracks: rail.tracks?.length,
    trackNames: (rail.tracks || []).map(t => t.name),
    trackProto: t0 ? Object.getOwnPropertyNames(Object.getPrototypeOf(t0)) : null,
    t0keys: t0 ? Object.keys(t0) : null,
    sectionKeys: secKeys,
    sample: secKeys.length ? (rail._sections.get(secKeys[0]) || []).slice(0, 4) : null,
    junctionCount: secKeys.reduce((n, k) => n + (rail._sections.get(k) || []).filter(s => s.junction).length, 0),
  };
}), null, 1));
await b.close();
