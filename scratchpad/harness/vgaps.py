#!/usr/bin/env python3
"""vgaps.py in.png x y w h — how much of a treeline crop is foliage, how much is
sky, and how much is neither.

"Neither" is the hazed ground showing through the wood, and it is the thing that
reads as a cutout: a real stand at this range is opaque, so the number wants to
be near zero. Run it on a reference crop and on ours at the same nominal range
and the difference is a measurement rather than an opinion.

Self-contained decoder for the same reason grade.py has one — the project ships
no image code and a lab bench has no Pillow.
"""
import struct
import subprocess
import sys
import tempfile
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def read_png(path):
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'not a png'
    pos, idat = 8, b''
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            w, h, depth, colour = struct.unpack('>IIBB', body[:10])
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
        pos += 12 + length
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(idat)
    stride = w * ch
    rows, prev, at = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[at]; at += 1
        line = bytearray(raw[at:at + stride]); at += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                ul = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(left, prev[i], ul)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, ch, rows


def load(path):
    if path.lower().endswith(('.jpg', '.jpeg')):
        out = tempfile.NamedTemporaryFile(suffix='.png', delete=False).name
        subprocess.run(['sips', '-s', 'format', 'png', path, '--out', out],
                       capture_output=True, check=True)
        path = out
    return read_png(path)


def main():
    path = sys.argv[1]
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    W, H, ch, rows = load(path)
    # A mask image fixes WHICH pixels count as foliage, which matters the moment
    # the thing being swept is how bright foliage is: classify per frame and a
    # change that darkens leaves quietly drops the darkest ones out of its own
    # average, so every setting measures about the same and nothing looks like
    # it did anything. Classify once on the baseline, measure everywhere.
    mrows = mch = None
    if len(sys.argv) > 6:
        _, _, mch, mrows = load(sys.argv[6])
    sky = fol = other = 0
    fr = fg = fb = 0
    lums = []
    for j in range(y, min(y + h, H)):
        row = rows[j]
        crow, cch = (mrows[j], mch) if mrows else (row, ch)
        for i in range(x, min(x + w, W)):
            o = i * ch
            r, g, b = row[o], row[o + 1], row[o + 2]
            co = i * cch
            cr, cg, cb = crow[co], crow[co + 1], crow[co + 2]
            lums.append(0.299 * r + 0.587 * g + 0.114 * b)
            if cb > cr + 10 and cb > 110:
                sky += 1
            elif cg > cr + 6 and cg > cb + 2:
                fol += 1
                fr += r; fg += g; fb += b
            else:
                other += 1
    n = sky + fol + other or 1
    lums.sort()
    q = lambda t: lums[int(t * (len(lums) - 1))]
    print(f'{path.split("/")[-1]}  crop {x},{y} {w}x{h}')
    print(f'  sky {100 * sky / n:5.1f}%   foliage {100 * fol / n:5.1f}%   '
          f'through {100 * other / n:5.1f}%')
    if fol:
        print(f'  foliage RGB {fr / fol:5.1f}/{fg / fol:5.1f}/{fb / fol:5.1f}'
              f'   B-R {(fb - fr) / fol:+5.1f}')
    print(f'  crop L  p5 {q(0.05):5.1f}  p50 {q(0.5):5.1f}  p95 {q(0.95):5.1f}')


main()
