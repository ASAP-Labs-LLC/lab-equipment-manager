#!/bin/zsh
PY='/Users/rynatical/LAB-lem/LEM Web Server/.venv/bin/python'
if [ -n "$1" ]; then FOGARG=(--fog "$1"); else FOGARG=(); fi
node sk-mainedge.mjs --cam far --time 9 "${FOGARG[@]}" 2>/dev/null | "$PY" -c "
import sys,json
d=json.load(sys.stdin)
p=d['profile']
n=len(p)
print('  chunk',d['chunk'],' density %.6f'%d['density'])
print('  dist %d  hterm %.3f  seaFogFactor %.4f  mainHazeFoot %.3f'%(
  sum(r['dist'] for r in p)/n, sum(r['hterm'] for r in p)/n,
  sum(r['seaFogFactor'] for r in p)/n, sum(r['mainHazeFoot'] for r in p)/n))
print('  land L %.1f  sea L %.1f  STEP L %.1f   landBR %.1f seaBR %.1f  stepBR %.1f'%(
  sum(r['land'] for r in p)/n, sum(r['sea'] for r in p)/n, sum(r['stepL'] for r in p)/n,
  sum(r['landBR'] for r in p)/n, sum(r['seaBR'] for r in p)/n,
  sum(r['landBR']-r['seaBR'] for r in p)/n))
"
