"""Did the extra haze cost the water its bathymetric read?

The frame is praised for the shelf-to-deep gradient around the island, so any
haze change has to be checked against it.  Mask on a FOG-OFF frame (bluish, not
canopy), keep only rows below the mainland's shoreline so the headland and the
sky cannot get in, then report the L distribution of the SAME pixels in each
fog-on frame.  p90-p10 is the shelf-to-deep spread; if it survives, the read
survives.

  python sk-water.py off.png ymin on1.png [on2.png ...]
"""
import sys
from PIL import Image

offp = sys.argv[1]; ymin = int(sys.argv[2]); ons = sys.argv[3:]
off = Image.open(offp).convert("RGB"); W, H = off.size
o = off.load()
pts = []
for y in range(ymin, H, 2):
    for x in range(0, W, 2):
        r, g, b = o[x, y]
        if b > r + 12 and b >= g - 4 and not (g > r + 8 and g > b + 8):
            pts.append((x, y))
print(f"water mask from {offp}: {len(pts)} px, rows {ymin}..{H}")
print(f"{'frame':>18} {'p10':>7} {'p50':>7} {'p90':>7} {'p90-p10':>8} {'B-R':>7}")
for p in [offp] + ons:
    px = Image.open(p).convert("RGB").load()
    Ls = []; br = 0
    for x, y in pts:
        r, g, b = px[x, y]
        Ls.append(0.2126 * r + 0.7152 * g + 0.0722 * b); br += b - r
    Ls.sort(); n = len(Ls)
    print(f"{p.split('/')[-1]:>18} {Ls[n//10]:7.1f} {Ls[n//2]:7.1f} {Ls[9*n//10]:7.1f} "
          f"{Ls[9*n//10]-Ls[n//10]:8.1f} {br/n:+7.1f}")
