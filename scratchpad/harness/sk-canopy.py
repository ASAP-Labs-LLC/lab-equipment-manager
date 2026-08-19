"""The critic's actual question: do the TREES recede?

sk-vband.py / fogmap.py average a 32x32 cell, so a distant "canopy" cell is
half sea and the ladder they print is mostly the sea's.  This masks to canopy
PIXELS and takes the distance from the cell map, so neither half of the
measurement is guessing:

  * the mask is built on a FOG-OFF frame (skyfog --pin 1e-9) of the same world,
    where a tree is unambiguously green (G > R and G > B by a margin) and the
    haze cannot have moved it.  The same pixel coordinates are then read out of
    every fog-on frame, so every config is measured on identical pixels.
  * the distance is the map cell's median geometry distance, and only cells
    whose dominant geometry is instanced foliage are used.

  python sk-canopy.py off.png map.json on1.png [on2.png ...] [--edges a,b,c]
"""
import json, sys
from PIL import Image

args = [a for a in sys.argv[1:]]
def opt(f, d=None):
    if f in args:
        i = args.index(f); v = args[i + 1]; del args[i:i + 2]; return v
    return d
edges = opt('--edges', '550,700,850,1000,1150,1300')
margin = int(opt('--margin', '8'))
E = [int(x) for x in edges.split(',')]
BANDS = list(zip(E[:-1], E[1:]))

offp, mapf = args[0], args[1]
ons = args[2:]

cells = json.load(open(mapf))
if not isinstance(cells, list):
    cells = next(v for v in cells.values() if isinstance(v, list))

off = Image.open(offp).convert("RGB")
W, H = off.size
opx = off.load()

# pixel -> band, from cells whose geometry is instanced foliage
sel = {b: [] for b in BANDS}
for c in cells:
    d = c.get("dist")
    if not d or "instanced" not in c.get("what", ""):
        continue
    band = None
    for e in BANDS:
        if e[0] <= d < e[1]:
            band = e
    if band is None:
        continue
    x0, y0 = int(c["x"]), int(c["y"])
    for y in range(y0, min(H, y0 + 32)):
        for x in range(x0, min(W, x0 + 32)):
            r, g, b = opx[x, y]
            if g > r + margin and g > b + margin:
                sel[band].append((x, y))

print(f"canopy mask from {offp}  (G>R+{margin} and G>B+{margin})")
hdr = f"{'band':>13} {'px':>6} |"
for p in [offp] + ons:
    hdr += f" {p.split('/')[-1][:11]:>22} |"
print(hdr)
print(f"{'':>13} {'':>6} |" + "".join(f" {'L':>6} {'B-R':>6} {'sat':>6} |" for _ in [offp] + ons))
for e in BANDS:
    pts = sel[e]
    if not pts:
        print(f"{e[0]:5d}-{e[1]:5d} {0:6d} |")
        continue
    row = f"{e[0]:5d}-{e[1]:5d} {len(pts):6d} |"
    for p in [offp] + ons:
        px = Image.open(p).convert("RGB").load()
        n = len(pts)
        r = sum(px[x, y][0] for x, y in pts) / n
        g = sum(px[x, y][1] for x, y in pts) / n
        b = sum(px[x, y][2] for x, y in pts) / n
        L = 0.2126 * r + 0.7152 * g + 0.0722 * b
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / mx * 100 if mx else 0
        row += f" {L:6.1f} {b - r:+6.1f} {sat:6.1f} |"
    print(row)
