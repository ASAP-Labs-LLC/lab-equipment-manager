#!/usr/bin/env python3
"""Measure only the foliage pixels in a crop: everything that is not sky and not
ground. Sky is blue-dominant and bright; ground here is warm and bright. Foliage
is the dark, green-leaning remainder — so the mask is 'luma below a cut'."""
import sys
sys.path.insert(0, '/Users/rynatical/LAB-lem/scratchpad/harness')
import grade

def load(p):
    d = grade.read_png(p)
    if d is None:
        d = grade.read_jpeg(p)
    return d

for path in sys.argv[1:]:
    px = load(path)
    if px and isinstance(px[0], (list, tuple)):
        px = [c for t in px for c in t[:3]]
    if px is None: continue
    # collect luma
    lum = []
    for i in range(0, len(px), 3):
        lum.append(0.299*px[i] + 0.587*px[i+1] + 0.114*px[i+2])
    lum_s = sorted(lum)
    thresh = lum_s[int(len(lum_s)*0.45)]     # darkest 45% = the tree body
    r=g=b=n=0
    for i in range(0, len(px), 3):
        if 0.299*px[i]+0.587*px[i+1]+0.114*px[i+2] <= thresh:
            r+=px[i]; g+=px[i+1]; b+=px[i+2]; n+=1
    if not n: continue
    print(f"{path:44s} foliage-only  R{r/n:6.1f} G{g/n:6.1f} B{b/n:6.1f}  "
          f"B-R {(b-r)/n:+6.1f}  G-max {'YES' if g>=r and g>=b else 'no ':3s}  "
          f"B<=R {'YES' if b<=r else 'no'}  n={n}")
