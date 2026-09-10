"""Write ui/public/icon-192.png and icon-512.png with nothing but the standard
library: no Pillow, no canvas, no build step.

The mark is the app's own telescope reticle - a ring, four ticks and a centre
pip in the accent, on the app background - so the installed icon and the browser
tab favicon are the same drawing.

Run from the repo root:

    python ui/src/next/shell/make-icons.py
"""
import math
import struct
import zlib

BG = (0x06, 0x07, 0x0B)
INK = (0x00, 0xD2, 0xFF)


def render(size: int) -> bytes:
    s = size
    # Supersample 3x and box-filter down: a hand-rolled rasteriser with no
    # antialiasing shows every stair step at 192 px on a phone home screen.
    ss = 3
    n = s * ss
    cx = cy = (n - 1) / 2.0
    ring_r = n * 0.30
    ring_w = n * 0.055
    pip_r = n * 0.065
    tick_in = n * 0.36
    tick_out = n * 0.46
    tick_w = n * 0.05

    # coverage mask of the ink, at supersampled resolution
    cov = bytearray(n * n)
    for y in range(n):
        dy = y - cy
        for x in range(n):
            dx = x - cx
            d = math.hypot(dx, dy)
            on = abs(d - ring_r) <= ring_w / 2 or d <= pip_r
            if not on and tick_in <= d <= tick_out:
                # four ticks on the axes
                on = abs(dx) <= tick_w / 2 or abs(dy) <= tick_w / 2
            if on:
                cov[y * n + x] = 1

    rows = []
    for y in range(s):
        row = bytearray()
        row.append(0)  # filter type 0 (None)
        for x in range(s):
            acc = 0
            for sy in range(ss):
                base = (y * ss + sy) * n + x * ss
                for sx in range(ss):
                    acc += cov[base + sx]
            a = acc / (ss * ss)
            for c in range(3):
                row.append(int(round(BG[c] * (1 - a) + INK[c] * a)))
            row.append(255)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


for px in (192, 512):
    with open("ui/public/icon-%d.png" % px, "wb") as f:
        f.write(render(px))
    print("wrote ui/public/icon-%d.png" % px)
