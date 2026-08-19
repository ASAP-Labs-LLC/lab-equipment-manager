#!/usr/bin/env python3
"""vthru.py withveg.png noveg.png x y w h [tol] — how much of a canopy crop is
not canopy at all but the hazed hillside showing through it, and what the two
populations measure separately. A distant wood that lets a third of the
background through is pale for a reason no tint can fix."""
import importlib.util
import sys

_spec = importlib.util.spec_from_file_location(
    'cropmod', '/Users/rynatical/LAB-lem/scratchpad/harness/crop.py')
_m = importlib.util.module_from_spec(_spec)
_m.__name__ = 'cropmod'
sys.argv, _keep = ['crop.py'], sys.argv
try:
    _spec.loader.exec_module(_m)
except SystemExit:
    pass
except IndexError:
    pass
sys.argv = _keep
read_png = _m.read_png

aw, ah, ach, arows = read_png(sys.argv[1])
bw, bh, bch, brows = read_png(sys.argv[2])
x, y, w, h = (int(v) for v in sys.argv[3:7])
tol = int(sys.argv[7]) if len(sys.argv) > 7 else 6
thru, cov = [], []
for j in range(y, y + h):
    ra, rb = arows[j], brows[j]
    for i in range(x, x + w):
        pa = (ra[i * ach], ra[i * ach + 1], ra[i * ach + 2])
        pb = (rb[i * bch], rb[i * bch + 1], rb[i * bch + 2])
        (thru if max(abs(pa[k] - pb[k]) for k in range(3)) <= tol
         else cov).append(pa)


def stat(px, name):
    if not px:
        print(f'{name:12s} n=0')
        return
    n = len(px)
    r = sum(p[0] for p in px) / n
    g = sum(p[1] for p in px) / n
    bl = sum(p[2] for p in px) / n
    print(f'{name:12s} {n * 100.0 / (w * h):5.1f}%  '
          f'{r:5.1f}/{g:5.1f}/{bl:5.1f}  B-R {bl - r:+6.1f}')


print(f'{sys.argv[1].split("/")[-1]:26s} crop {w}x{h} at {x},{y} tol={tol}')
stat(thru, ' background')
stat(cov, ' covered')
stat(thru + cov, ' all')
