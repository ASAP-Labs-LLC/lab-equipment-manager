import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
p.on('console', m => { const t=m.text(); if (!/favicon|404/.test(t)) console.log(m.type().toUpperCase(), t.slice(0,200)); });
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, e = w.engine, gi = w.subsystems.get('gi');
  const before = {tier: e.tier.name, casters: gi._csm.map(c=>c.casters.length), adoptClock: gi._adoptClock};
  e.setTier(1, {force: true});
  await new Promise(r => setTimeout(r, 4000));
  let sample = null;
  w.scene.traverse(o => { if (!sample && o.userData?.lemCast && o.userData.lemCastBase) sample = {
    size: o.userData.lemCast.size, rise: o.userData.lemCast.rise, slab: o.userData.lemCast.slab,
    layers: o.layers.mask, cast: o.castShadow}; });
  return {before, after: {tier: e.tier.name, n: gi._csm.length,
    casters: gi._csm.map(c=>c.casters.length), depth: !!gi._depthOpaque,
    adoptClock: gi._adoptClock, built: gi._built, modeKey: gi._modeKey}, sample};
}), null, 1));
await b.close();
