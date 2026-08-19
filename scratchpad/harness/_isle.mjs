import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=wide&time=9&hud=0&quality=ultra', {waitUntil:'load',timeout:90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const cx=t.cx, cz=t.cz, R=(t.islandR||480)*1.05;
  let n=0, s=0, sq=0, mx=0, gateSum=0;
  const S=6;
  const grid=[];
  for (let j=-70;j<=70;j++){ const row=[]; for (let i=-70;i<=70;i++){
    const x=cx+i*R/70, z=cz+j*R/70;
    const sd=t._islandSD(x,z);
    row.push(sd<0 ? t._islandForm(x,z,sd) : NaN);
  } grid.push(row);}
  let gs=0, gn=0, gmax=0;
  for (let j=1;j<grid.length-1;j++) for(let i=1;i<grid[0].length-1;i++){
    const v=grid[j][i]; if(!isFinite(v)) continue;
    n++; s+=v; sq+=v*v; mx=Math.max(mx,Math.abs(v));
    const a=grid[j][i+1],b2=grid[j][i-1],c=grid[j+1][i],d=grid[j-1][i];
    if([a,b2,c,d].every(isFinite)){
      const step=R/70;
      const dx=(a-b2)/(2*step), dz=(c-d)/(2*step);
      const g=Math.hypot(dx,dz); gs+=Math.atan(g)*180/Math.PI; gn++; gmax=Math.max(gmax,g);
    }
  }
  const mean=s/n;
  return {n, meanIsle:+mean.toFixed(2), rmsIsle:+Math.sqrt(sq/n-mean*mean).toFixed(2),
          maxIsle:+mx.toFixed(2), meanSlopeIsleDeg:+(gs/gn).toFixed(2),
          maxGradIsle:+gmax.toFixed(2), sampleStep:+(R/70).toFixed(1),
          islandR:+t.islandR.toFixed(0), waterY:+t.waterY.toFixed(1)};
})));
await b.close();
