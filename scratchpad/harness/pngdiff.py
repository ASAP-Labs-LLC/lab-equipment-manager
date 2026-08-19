#!/usr/bin/env python3
"""pngdiff.py a.png b.png [out.png] — how much of the frame actually moved.

Reports mean and p99 absolute luminance delta and the share of pixels past a
few thresholds, because "I wired it up" and "a critic can see it" are different
claims and only the second one counts.
"""
import sys
import zlib
import struct
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from wbcrop import read_png

wa, ha, ca, pa = read_png(sys.argv[1])
wb, hb, cb, pb = read_png(sys.argv[2])
assert (wa, ha) == (wb, hb), 'different sizes'
n = wa * ha
deltas = []
out = bytearray(n * 3)
for i in range(n):
    oa, ob = i * ca, i * cb
    la = 0.2126 * pa[oa] + 0.7152 * pa[oa + 1] + 0.0722 * pa[oa + 2]
    lb = 0.2126 * pb[ob] + 0.7152 * pb[ob + 1] + 0.0722 * pb[ob + 2]
    d = abs(la - lb)
    deltas.append(d)
    v = min(255, int(d * 6))
    out[i * 3] = out[i * 3 + 1] = out[i * 3 + 2] = v
deltas.sort()
print(f'mean {sum(deltas) / n:.2f}/255   p50 {deltas[n // 2]:.1f}   '
      f'p95 {deltas[int(n * .95)]:.1f}   p99 {deltas[int(n * .99)]:.1f}   '
      f'max {deltas[-1]:.1f}')
for t in (2, 5, 10, 20):
    share = sum(1 for d in deltas if d >= t) / n * 100
    print(f'  >= {t:>2}/255 : {share:5.2f}% of pixels')

if len(sys.argv) > 3:
    raw = b''.join(b'\0' + bytes(out[y * wa * 3:(y + 1) * wa * 3]) for y in range(ha))
    def chunk(k, b):
        c = k + b
        return struct.pack('>I', len(b)) + c + struct.pack('>I', zlib.crc32(c))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', wa, ha, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    open(sys.argv[3], 'wb').write(png)
    print('wrote', sys.argv[3])
