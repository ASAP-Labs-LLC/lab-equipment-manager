#!/usr/bin/env python3
"""wbcrop.py img.png x,y,w,h [more...] — mean RGB over rectangles of a
screenshot, so a claim about foliage colour is a number and not an impression.

Prints PASS when the acceptance condition holds on that rectangle: green is the
largest channel and blue does not exceed red.
"""
import struct
import sys
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def read_png(path):
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
    pos, idat, ch = 8, b'', 3
    w = h = None
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            w, h, depth, colour = struct.unpack('>IIBB', body[:10])
            assert depth == 8 and colour in (2, 6), 'unsupported PNG'
            ch = 3 if colour == 2 else 4
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * ch
    out = bytearray(stride * h)
    prev = bytearray(stride)
    at = 0
    for y in range(h):
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
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


if __name__ == '__main__':
  w, h, ch, px = read_png(sys.argv[1])
  for spec in sys.argv[2:]:
      x0, y0, cw, chh = (int(v) for v in spec.split(','))
      n = r = g = b = 0
      for y in range(max(0, y0), min(h, y0 + chh)):
          base = y * w * ch
          for x in range(max(0, x0), min(w, x0 + cw)):
              o = base + x * ch
              r += px[o]; g += px[o + 1]; b += px[o + 2]
              n += 1
      n = max(1, n)
      r, g, b = r / n, g / n, b / n
      ok = 'PASS' if (g > r and g > b and b <= r) else 'fail'
      print(f'{spec:<22} R{r:6.1f} G{g:6.1f} B{b:6.1f}   B-R {b - r:+6.1f}   {ok}')
