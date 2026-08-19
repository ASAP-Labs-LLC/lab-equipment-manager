"""Finer B-R ladder by TRUE per-cell distance, split by what the cell holds.

fogmap.py's five bands are too coarse to see whether the ISLAND recedes: at
cam=far the island's own canopy is almost all inside one of them.  Same input
(skyfog --map), same crop, finer edges, and a --only filter on the cell's
`what` so the instanced-foliage pass can be read on its own.

  python sk-vband.py shot.png shot.map.json [--only instanced] [--edges a,b,c]
"""
import json, sys
from PIL import Image

png, mapf = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
def opt(f, d=None):
    return args[args.index(f) + 1] if f in args else d
only = opt('--only')
edges = opt('--edges', '300,450,600,750,900,1100,1400,2000,6000')
E = [int(x) for x in edges.split(',')]
BANDS = list(zip(E[:-1], E[1:]))

im = Image.open(png).convert("RGB"); W, H = im.size
cells = json.load(open(mapf))
if not isinstance(cells, list):
    cells = next(v for v in cells.values() if isinstance(v, list))

acc = {b: [] for b in BANDS}
kinds = {}
for c in cells:
    d = c.get("dist")
    if not d:
        continue
    what = c.get("what", "?")
    kinds.setdefault(what, []).append(d)
    if only and only not in what:
        continue
    x, y = int(c["x"]), int(c["y"])
    box = im.crop((max(0, x - 16), max(0, y - 16), min(W, x + 16), min(H, y + 16)))
    px = list(box.getdata())
    if not px:
        continue
    n = len(px)
    r = sum(p[0] for p in px) / n
    g = sum(p[1] for p in px) / n
    b = sum(p[2] for p in px) / n
    for e in BANDS:
        if e[0] <= d < e[1]:
            acc[e].append((r, g, b))

print(f"{png}   only={only or '(all)'}")
print(f"{'band':>14}  {'cells':>5}  {'R':>5} {'G':>5} {'B':>5}   {'L':>6}  {'B-R':>6}")
prev = None
for e in BANDS:
    v = acc[e]
    if not v:
        print(f"{e[0]:6d}-{e[1]:5d}      -")
        continue
    r = sum(x[0] for x in v) / len(v); g = sum(x[1] for x in v) / len(v)
    b = sum(x[2] for x in v) / len(v)
    L = 0.2126 * r + 0.7152 * g + 0.0722 * b
    d = ''
    if prev is not None:
        d = f"   dL {L - prev[0]:+6.1f}  dBR {(b - r) - prev[1]:+6.1f}"
    print(f"{e[0]:6d}-{e[1]:5d}  {len(v):5d}  {r:5.1f} {g:5.1f} {b:5.1f}  "
          f"{L:6.1f}  {b - r:+6.1f}{d}")
    prev = (L, b - r)
if '--kinds' in args:
    print("\n  what                       cells   dist min/med/max")
    for k, ds in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        ds.sort()
        print(f"  {k:26s} {len(ds):5d}   {ds[0]:5d} {ds[len(ds)//2]:5d} {ds[-1]:5d}")
