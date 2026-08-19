#!/usr/bin/env python3
"""stipple.py x y w h file.png… — a number for "speckled".

A screen door and a wood differ in where their energy sits: a stand of trees
varies over crowns, tens of pixels wide, while a dithered alpha test varies
between one pixel and the next. So the statistic is the mean absolute
difference between horizontally adjacent pixels (high-frequency energy) shown
against the crop's own standard deviation (total energy). A canopy that is
speckled has the first close to the second; a canopy that is a mass has it far
below. Also reports the fraction of pixels that are local outliers — brighter
than both neighbours by more than eight levels — which is the white fleck
itself."""
import importlib.util
import sys

_spec = importlib.util.spec_from_file_location(
    'cropmod', '/Users/rynatical/LAB-lem/scratchpad/harness/crop.py')
_m = importlib.util.module_from_spec(_spec)
_keep, sys.argv = sys.argv, ['crop.py']
try:
    _spec.loader.exec_module(_m)
except (SystemExit, IndexError):
    pass
sys.argv = _keep
read_png = _m.read_png

x, y, w, h = (int(v) for v in sys.argv[1:5])
print(f'{"file":34s} {"lum":>6s} {"sigma":>7s} {"hfreq":>7s} {"hf/sig":>7s} {"flecks%":>8s}')
for path in sys.argv[5:]:
    iw, ih, ch, rows = read_png(path)
    lum, n, hf, hfn, sq, flecks = 0.0, 0, 0.0, 0, 0.0, 0
    vals = []
    for j in range(y, y + h):
        r = rows[j]
        line = [(r[i * ch] * 54 + r[i * ch + 1] * 183 + r[i * ch + 2] * 19) / 256.0
                for i in range(x, x + w)]
        vals.extend(line)
        for k in range(1, len(line)):
            hf += abs(line[k] - line[k - 1]); hfn += 1
        for k in range(1, len(line) - 1):
            if line[k] - line[k - 1] > 8 and line[k] - line[k + 1] > 8:
                flecks += 1
    n = len(vals)
    mean = sum(vals) / n
    sq = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    hf /= hfn
    print(f'{path.split("/")[-1]:34s} {mean:6.1f} {sq:7.2f} {hf:7.2f} '
          f'{hf / max(sq, 1e-6):7.3f} {flecks * 100.0 / n:8.2f}')
