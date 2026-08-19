/* vcrown.mjs — dump one crown tile from the live atlas at 1:1, on sky, plus its
 * mip chain, so "hard alpha fringes" can be looked at instead of argued about. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], out = process.argv[3];
const tile = Number(process.argv[4] ?? 20);      // crownTile(3,0) = oak variant 0
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:600}});
p.on('console', m => { if (m.type()==='error') console.log('ERR', m.text()); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(600);
const data = await p.evaluate(({tile}) => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.atlas.image;
  const GRID = 6, T = src.width / GRID;
  const c = tile % GRID, r = (tile / GRID) | 0;
  const W = 1400, H = 700;
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#b9cbd9'; g.fillRect(0,0,W,H);
  g.imageSmoothingEnabled = false;
  /* left: the tile 1:1-ish over sky */
  g.drawImage(src, c*T, r*T, T, T, 0, 0, 680, 680);
  /* right: successive box-downsamples = the mip chain, each blown back up */
  const cut = document.createElement('canvas'); cut.width = T; cut.height = T;
  cut.getContext('2d').drawImage(src, c*T, r*T, T, T, 0, 0, T, T);
  let cur = cut;
  let x = 700;
  for (let m = 1; m <= 5; m++) {
    const n = Math.max(1, Math.round(T / Math.pow(2, m)));
    const nc = document.createElement('canvas'); nc.width = n; nc.height = n;
    const ng = nc.getContext('2d');
    ng.imageSmoothingEnabled = true;
    ng.drawImage(cur, 0, 0, n, n);
    cur = nc;
    const size = m <= 2 ? 340 : 220;
    g.imageSmoothingEnabled = false;
    g.drawImage(nc, 0, 0, n, n, x, m<=2?0:360, size, size);
    g.fillStyle = '#000'; g.font = '16px sans-serif';
    g.fillText('mip'+m+' '+n+'px', x+4, (m<=2?0:360)+16);
    x += size + 10;
    if (m === 2) x = 700;
  }
  /* alpha histogram of mip4 */
  const n4 = cur.width;
  const px = cur.getContext('2d').getImageData(0,0,n4,n4).data;
  const hist = new Array(10).fill(0);
  for (let i=3;i<px.length;i+=4) hist[Math.min(9,(px[i]/25.6)|0)]++;
  return {png: cv.toDataURL('image/png'), hist, n4};
}, {tile});
fs.writeFileSync(out, Buffer.from(data.png.split(',')[1],'base64'));
console.log('mip5 alpha histogram (deciles):', JSON.stringify(data.hist), 'size', data.n4);
await b.close();
