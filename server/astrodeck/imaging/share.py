"""NOV-11 — one-tap "Save first light" share JPEG.

Two pure pieces, tested independently:

* ``build_caption`` (+ the tiny ``fmt_exposure``/``fmt_share_date`` formatters)
  — copy/format logic only, no image work.
* ``compose_share_jpeg`` — deterministic PIL geometry: decode the already-
  encoded display/lossless bytes, downscale (never upscale) to phone width,
  draw a caption band, re-encode as JPEG. This does NOT re-derive a stretch —
  Pass 1 ``entry.linear`` is always ``None``, so there is nothing to re-stretch
  from; the caption composites onto the encoded bytes processing.py already
  produced (see ``imaging/processing.py`` docstring).

Privacy: the caption is target / exposure / count / gain / date ONLY. It never
reads or renders a site location (strip-entirely posture, spec §2).
"""
from __future__ import annotations

import io
import time

from PIL import Image, ImageDraw, ImageFont


def fmt_exposure(exposure_s: float) -> str:
    """120.0 -> '120 s'; 1.5 -> '1.5 s'; 0.5 -> '0.5 s'; 0/negative -> '— s'."""
    e = float(exposure_s or 0)
    if e <= 0:
        return "— s"
    return f"{int(e)} s" if e == int(e) else f"{e:g} s"


def fmt_share_date(ts: float) -> str:
    """Server-local date, '%b %d %Y' with the day's leading zero stripped."""
    lt = time.localtime(ts)
    return f"{time.strftime('%b', lt)} {lt.tm_mday} {lt.tm_year}"


def _clean(s: str | None, cap: int) -> str:
    s = "".join(c for c in (s or "") if c.isprintable()).strip()
    return s[:cap]


def build_caption(target: str | None, exposure_s: float,
                  sub_count: int | None, date_str: str,
                  gain: float | int | None = None) -> tuple[str, str]:
    """(title, detail) — title = target (control-stripped, <=48 chars) or
    'First light'; detail = 'EXP [x N] [· gain G] · DATE'. Never includes a
    site location."""
    title = _clean(target, 48) or "First light"
    exp = fmt_exposure(exposure_s)
    if sub_count and int(sub_count) > 1:
        exp = f"{exp} × {int(sub_count)}"
    parts = [exp]
    if gain is not None:
        parts.append(f"gain {int(gain)}")
    parts.append(date_str)
    return title, " · ".join(parts)


def compose_share_jpeg(base: bytes, title: str, detail: str, *,
                       max_width: int = 1080, quality: int = 90,
                       wordmark: str = "AstroDeck") -> tuple[bytes, int, int]:
    """Composite a caption band onto already-encoded JPEG/PNG ``base`` bytes.

    Deterministic geometry (spec §1.3):
    1. decode + convert to RGB.
    2. downscale (BILINEAR) to ``max_width`` if wider; never upscale.
    3. fonts via ``ImageFont.load_default(px)`` (scalable on Pillow 12+).
    4. new canvas ``(w, h + band_h)`` filled ``(11,13,17)``; photo pasted at
       the top; a 1px accent line; title/detail drawn in the band; wordmark
       right-aligned in the detail row.
    5. re-encode as JPEG.

    Returns ``(jpeg_bytes, out_w, out_h)``.
    """
    img = Image.open(io.BytesIO(base)).convert("RGB")
    w, h = img.size
    if w > max_width:
        new_h = round(h * max_width / w)
        img = img.resize((max_width, new_h), Image.BILINEAR)
        w, h = img.size

    title_px = max(16, round(w / 26))
    detail_px = max(12, round(w / 40))
    title_font = ImageFont.load_default(title_px)
    detail_font = ImageFont.load_default(detail_px)
    pad = round(w / 45)
    gap = round(pad * 0.4)
    band_h = pad * 2 + title_px + gap + detail_px

    canvas = Image.new("RGB", (w, h + band_h), (11, 13, 17))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.line([(0, h), (w, h)], fill=(60, 80, 120), width=1)
    draw.text((pad, h + pad), title, fill=(238, 240, 245), font=title_font)
    detail_y = h + pad + title_px + gap
    draw.text((pad, detail_y), detail, fill=(150, 162, 180), font=detail_font)
    mark_w = draw.textlength(wordmark, font=detail_font)
    draw.text((w - pad - mark_w, detail_y), wordmark, fill=(90, 100, 120),
              font=detail_font)

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=quality, optimize=False)
    return buf.getvalue(), canvas.width, canvas.height
