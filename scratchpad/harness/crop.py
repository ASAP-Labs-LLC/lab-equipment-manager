#!/usr/bin/env python3
"""crop.py in.png out.png x y w h [zoom] — nearest-neighbour crop/zoom, so a
dark corner of a 1080p frame can actually be looked at. Self-contained: grade.py
subsamples on read, which is right for statistics and useless for looking."""
import struct
import sys
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
    assert depth == 8
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


def write_png(path, w, h, rgb):
    raw = b''.join(b'\x00' + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body +
                struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff))
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 6))
    png += chunk(b'IEND', b'')
    open(path, 'wb').write(png)


src, dst = sys.argv[1], sys.argv[2]
x, y, cw, chh = (int(v) for v in sys.argv[3:7])
z = int(sys.argv[7]) if len(sys.argv) > 7 else 1
W, H, ch, rows = read_png(src)
out = bytearray(cw * z * chh * z * 3)
for j in range(chh * z):
    row = rows[min(H - 1, y + j // z)]
    for i in range(cw * z):
        o = min(W - 1, x + i // z) * ch
        d = (j * cw * z + i) * 3
        out[d:d + 3] = row[o:o + 3]
write_png(dst, cw * z, chh * z, bytes(out))
print(f'{src} -> {dst} {cw*z}x{chh*z}')
