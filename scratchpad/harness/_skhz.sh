#!/bin/zsh
PY='/Users/rynatical/LAB-lem/LEM Web Server/.venv/bin/python'
CAM=$1
if [ -n "$2" ]; then FOGARG=(--fog "$2"); else FOGARG=(); fi
node sk-haze.mjs --cam $CAM --time 9 "${FOGARG[@]}" 2>/dev/null | "$PY" -c "
import sys,json
d=json.load(sys.stdin)
for b in d['dist']:
    if not b.get('n'): continue
    print('  %-12s n%-6d dist %5d dep %5d hterm %.3f  f %.4f  fB %.4f  L %6.1f L0 %6.1f'%(
      b['name'],b['n'],b['dist'],b['dep'],b['hterm'],b['f'],b['fB'],b['L'],b['L0']))
"
