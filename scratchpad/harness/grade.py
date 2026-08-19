#!/usr/bin/env python3
"""grade.py — measure the colour grade of a render, so "cooler and darker than
the reference" stops being an opinion.

    python3 grade.py ours.png refs/aftertheflood-03.png ...

Prints, per file: mean RGB, blue-minus-red bias, mean saturation, and the
luminance percentiles the reference manifest quotes. The bar, measured from
twelve live captures of After the Flood:

    mean L ≈ 77   p95 ≈ 140   B−R ≈ +25.6   mean saturation ≈ 25%

Transport Fever 2's gameplay look, for contrast: mean L 110.6, p95 213, B−R −2.8.
A render drifting from the first set toward the second is a different and less
controlled image, whatever it looks like in isolation.

No dependencies — PNG and JPEG are decoded here rather than pulling Pillow into
a project that ships no image code.
"""
import struct
import sys
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def read_png(path):
    """Minimal 8-bit RGB/RGBA non-interlaced PNG reader — what a browser
    screenshot always is."""
    data = open(path, 'rb').read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos, idat, w = 8, b'', None
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            w, h, depth, colour = struct.unpack('>IIBB', body[:10])
            if depth != 8 or colour not in (2, 6):
                return None
            channels = 3 if colour == 2 else 4
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * channels
    out, prev, at = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[at]; at += 1
        line = bytearray(raw[at:at + stride]); at += stride
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                ul = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, prev[i], ul)) & 255
        out.append(bytes(line))
        prev = line
    # Sample rather than measure every pixel: 1080p is 2M pixels and the
    # statistics below are stable long before that.
    px = []
    for y in range(0, h, max(1, h // 240)):
        row = out[y]
        for x in range(0, w, max(1, w // 320)):
            i = x * channels
            px.append((row[i], row[i + 1], row[i + 2]))
    return px


def read_jpeg(path):
    """JPEG needs a real decoder. Rather than write one, shell out to the
    system's — macOS always has `sips`, which can rewrite it as a PNG."""
    import subprocess, tempfile, os
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        out = tmp.name
    try:
        subprocess.run(['sips', '-s', 'format', 'png', path, '--out', out],
                       capture_output=True, check=True)
        return read_png(out)
    except Exception:
        return None
    finally:
        if os.path.exists(out):
            os.unlink(out)


def measure(path):
    px = read_jpeg(path) if path.lower().endswith(('.jpg', '.jpeg')) \
        else read_png(path)
    if not px:
        return None
    n = len(px)
    r = sum(p[0] for p in px) / n
    g = sum(p[1] for p in px) / n
    b = sum(p[2] for p in px) / n
    lum = sorted(0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2] for p in px)
    sat = []
    for p in px:
        hi, lo = max(p), min(p)
        sat.append(0 if hi == 0 else (hi - lo) / hi)
    pct = lambda q: lum[min(n - 1, int(n * q))]
    mean_l = sum(lum) / n
    var = sum((v - mean_l) ** 2 for v in lum) / n
    return {'rgb': (r, g, b), 'br': b - r, 'sat': sum(sat) / n * 100,
            'meanL': mean_l, 'sigma': var ** 0.5,
            'p1': pct(0.01), 'p50': pct(0.5), 'p95': pct(0.95)}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(f"{'file':<34} {'meanRGB':>16} {'B-R':>7} {'sat%':>6} "
          f"{'meanL':>6} {'sigma':>6} {'p1':>5} {'p50':>5} {'p95':>5}")
    for path in sys.argv[1:]:
        m = measure(path)
        name = path.split('/')[-1][:33]
        if not m:
            print(f'{name:<34}   (could not decode)')
            continue
        rgb = '/'.join(f'{v:.0f}' for v in m['rgb'])
        print(f"{name:<34} {rgb:>16} {m['br']:>+7.1f} {m['sat']:>6.1f} "
              f"{m['meanL']:>6.1f} {m['sigma']:>6.1f} {m['p1']:>5.0f} "
              f"{m['p50']:>5.0f} {m['p95']:>5.0f}")
