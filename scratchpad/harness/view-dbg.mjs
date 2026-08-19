import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1600,height:950}})).newPage();
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000});
await p.waitForTimeout(13000);
await p.click('#btnView'); await p.waitForTimeout(1200);
console.log(JSON.stringify(await p.evaluate(()=>{
  const c=document.getElementById('world'), s=document.getElementById('floorSimple');
  const g=document.querySelector('#floorSimple .simple-machine');
  const cs=getComputedStyle(c), ss=getComputedStyle(s);
  const sb=s.getBoundingClientRect(), gb=g?g.getBoundingClientRect():null;
  // what is actually on top at the centre of the first block?
  let topEl=null;
  if(gb) topEl=document.elementFromPoint(gb.x+gb.width/2, gb.y+gb.height/2);
  return {canvasDisplay:cs.display, canvasHidden:c.hidden,
          svgDisplay:ss.display, svgBox:[sb.width|0, sb.height|0],
          firstBlockBox: gb?[gb.x|0,gb.y|0,gb.width|0,gb.height|0]:null,
          viewBox: s.getAttribute('viewBox'),
          topElementAtBlock: topEl? (topEl.id||topEl.tagName+'.'+topEl.getAttribute('class')) : null};
}),null,1));
await b.close();
