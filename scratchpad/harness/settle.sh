#!/bin/zsh
# settle.sh — poll the site until it is worth photographing.  See NOTES-gi.md.
cd "$(dirname "$0")"
MODS="sky,gi,terrain,buildings,rail,trains,vegetation,weather"
for i in $(seq 1 ${1:-40}); do
  s=$(node shot.mjs \
    --url "http://127.0.0.1:5601/static/world/dev/solo.html?mods=$MODS&cam=yard&time=16&weather=clear&hud=0" \
    --out ../shots/r8-settle.png --seconds 4 --quality ultra 2>&1 | \
    python3 -c "import sys,json;t=sys.stdin.read();i=t.find('{');d=json.loads(t[i:]);print(d['drawCalls'],d['triangles'],len(d['errors']),len(d['failed']))")
  g=$(python3 grade.py ../shots/r8-settle.png 2>/dev/null | tail -1)
  echo "$(date +%H:%M:%S) try $i  draws/tris/err/fail=[$s]  grade=[$g]"
  print -r -- "$s|$g" | python3 -c "
import sys
s,g=sys.stdin.read().split('|')
d,t,e,f=(int(x) for x in s.split())
c=g.split()
meanL=float(c[4]); sigma=float(c[5]); p1=int(c[6]); p50=int(c[7]); p95=int(c[8])
ok = e==0 and f==0 and t>900000 and sigma>35 and 60<meanL<170 and p95<250
sys.exit(0 if ok else 1)
" && { echo SETTLED; exit 0; }
  sleep 40
done
exit 1
