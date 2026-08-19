#!/usr/bin/env python3
"""fogbands.py <fog-off.png> <frame.png> [more.png ...] [--crops]

Blue-minus-red on *foliage only*, in bands of known range.

Two things make this test honest, both learned the hard way:

  * The band rects come from `skyfog.mjs --map`, which projects every scene
    instance to the screen; the median range of what falls in each band is in
    its name. An earlier round of this exact test measured sky by mistake and
    reported a pass.

  * The foliage mask is taken from a **fog-off** frame of the identical scene
    (`skyfog.mjs --pin 0`), where canopy is unambiguous — green largest, and
    dark. The same pixels are then measured in the fogged frames. Terrain,
    road and sky inside the band cannot contaminate the number, and a change
    in the haze cannot move the mask.

Self-contained: grade.py subsamples on read, which is right for whole-frame
statistics and useless for a 32-pixel band.
"""
import os
import struct
import sys
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def read_png(path):
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', path + ' is not a png'
    pos, idat = 8, b''
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            w, h, depth, colour = struct.unpack('>IIBB', body[:10])
            ch = 3 if colour == 2 else 4
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride, rows, prev, at = w * ch, [], bytearray(w * ch), 0
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


def write_png(path, w, h, rgb):
    raw = b''.join(b'\x00' + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body +
                struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff))
    out = b'\x89PNG\r\n\x1a\n'
    out += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    out += chunk(b'IDAT', zlib.compress(raw, 6))
    out += chunk(b'IEND', b'')
    open(path, 'wb').write(out)


# name, x, y, w, h — y ladder read off the projected instance map for cam=wide.
BANDS = [
    ('near-250m', 0, 800, 1920, 200),
    ('mid-560m', 0, 288, 1920, 64),
    ('far-780m', 0, 224, 1920, 32),
    ('far-1130m', 0, 96, 1920, 32),
    ('far-1360m', 0, 32, 1920, 64),
]


def mask_of(rows, ch, w, h, band):
    """Foliage in the fog-off frame: green largest by a real margin and not
    bright. A ridge of bare terrain is warm, the sky is pale; neither passes."""
    _, x0, y0, bw, bh = band
    out = []
    for y in range(y0, min(y0 + bh, h)):
        row = rows[y]
        for x in range(x0, min(x0 + bw, w)):
            i = x * ch
            r, g, b = row[i], row[i + 1], row[i + 2]
            if g > r + 4 and g > b + 4 and g < 150:
                out.append((x, y))
    return out


def main():
    global BANDS
    for a in sys.argv[1:]:
        if a.startswith('--bands='):          # name:x:y:w:h,name:x:y:w:h
            BANDS = [(p.split(':')[0],) + tuple(int(v) for v in p.split(':')[1:])
                     for p in a[8:].split(',')]
    files = [a for a in sys.argv[1:] if not a.startswith('--')]
    ref = files[0]
    w, h, ch, rows0 = read_png(ref)
    masks = {}
    print('mask frame: %s' % os.path.basename(ref))
    for band in BANDS:
        masks[band[0]] = mask_of(rows0, ch, w, h, band)
        px = masks[band[0]]
        tr = sum(rows0[y][x * ch] for x, y in px) / max(1, len(px))
        tg = sum(rows0[y][x * ch + 1] for x, y in px) / max(1, len(px))
        tb = sum(rows0[y][x * ch + 2] for x, y in px) / max(1, len(px))
        print('  %-10s %6d px  unfogged %3.0f/%3.0f/%3.0f  B-R %+6.1f'
              % (band[0], len(px), tr, tg, tb, tb - tr))

    for path in files:
        w, h, ch, rows = read_png(path)
        print(os.path.basename(path))
        for band in BANDS:
            px = masks[band[0]]
            if not px:
                continue
            tr = sum(rows[y][x * ch] for x, y in px) / len(px)
            tg = sum(rows[y][x * ch + 1] for x, y in px) / len(px)
            tb = sum(rows[y][x * ch + 2] for x, y in px) / len(px)
            print('  %-10s %3.0f/%3.0f/%3.0f  B-R %+6.1f  G largest: %s'
                  % (band[0], tr, tg, tb, tb - tr,
                     'yes' if tg >= tr and tg >= tb else 'NO'))
        if '--crops' in sys.argv:
            for name, x0, y0, bw, bh in BANDS:
                z = 1 if bw > 960 else 2
                buf = bytearray(bw * z * bh * z * 3)
                for j in range(bh * z):
                    row = rows[min(h - 1, y0 + j // z)]
                    for i in range(bw * z):
                        o = min(w - 1, x0 + i // z) * ch
                        d = (j * bw * z + i) * 3
                        buf[d:d + 3] = row[o:o + 3]
                write_png(path[:-4] + '-' + name + '.png', bw * z, bh * z, bytes(buf))


main()
