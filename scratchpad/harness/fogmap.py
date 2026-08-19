"""B-R against real per-cell distance, from skyfog's depth map.

fogbands.py masks by colour and silently reports zero pixels for bands that do
contain geometry -- it called 780 m+ empty on a frame holding mainland at
1733 m. The map already carries a distance per 32x32 cell, so use it.
"""
import json, sys
from PIL import Image

png, mapf = sys.argv[1], sys.argv[2]
im = Image.open(png).convert("RGB"); W, H = im.size
cells = json.load(open(mapf))
if not isinstance(cells, list):
    cells = next(v for v in cells.values() if isinstance(v, list))

EDGES = [(0, 300), (300, 600), (600, 900), (900, 1300), (1300, 2000)]
bands = {e: [] for e in EDGES}
for c in cells:
    d = c.get("dist")
    if not d:
        continue
    x, y = int(c["x"]), int(c["y"])
    box = im.crop((max(0, x - 16), max(0, y - 16),
                   min(W, x + 16), min(H, y + 16)))
    px = list(box.getdata())
    if not px:
        continue
    r = sum(p[0] for p in px) / len(px)
    g = sum(p[1] for p in px) / len(px)
    b = sum(p[2] for p in px) / len(px)
    for e in EDGES:
        if e[0] <= d < e[1]:
            bands[e].append((r, g, b, d, c.get("what", "?")))

print(f"{png}")
for e in EDGES:
    v = bands[e]
    if not v:
        print(f"  {e[0]:5d}-{e[1]:5d} m   (no geometry)")
        continue
    r = sum(x[0] for x in v) / len(v); g = sum(x[1] for x in v) / len(v)
    b = sum(x[2] for x in v) / len(v)
    kinds = {}
    for x in v:
        kinds[x[4]] = kinds.get(x[4], 0) + 1
    top = max(kinds, key=kinds.get)
    print(f"  {e[0]:5d}-{e[1]:5d} m  {len(v):4d} cells  "
          f"RGB {r:5.1f}/{g:5.1f}/{b:5.1f}   B-R {b - r:+6.1f}   mostly {top}")
