"""NOV-11 — save/share first-light JPEG: caption formatters + compositor."""
import io
import re

import numpy as np
from PIL import Image

from astrodeck.imaging import build_caption, compose_share_jpeg, fmt_exposure, fmt_share_date, to_jpeg


def test_fmt_exposure():
    assert fmt_exposure(120.0) == "120 s"
    assert fmt_exposure(1.5) == "1.5 s"
    assert fmt_exposure(0.5) == "0.5 s"
    assert fmt_exposure(0) == "— s"


def test_fmt_share_date_shape():
    s = fmt_share_date(1_753_000_000.0)   # some 2025 epoch
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2} \d{4}", s), s
    assert "  " not in s                  # leading day-zero stripped


def test_build_caption_full():
    t, d = build_caption("M42", 120.0, 30, "Jul 22 2026", 100)
    assert t == "M42"
    assert d == "120 s × 30 · gain 100 · Jul 22 2026"


def test_build_caption_single_no_gain():
    t, d = build_caption(None, 5.0, 1, "Jul 22 2026", None)
    assert t == "First light"
    assert d == "5 s · Jul 22 2026"          # no "× 1", no gain


def test_build_caption_strips_and_caps():
    t, _ = build_caption("A" * 80 + "\n\t", 1.0, 0, "x", None)
    assert len(t) <= 48 and "\n" not in t and "\t" not in t


# ----------------------------------------------------------- compose_share_jpeg

def _base_jpeg(w=1600, h=900):
    grad = np.tile(np.linspace(0, 65535, w, dtype=np.uint16), (h, 1))
    return to_jpeg(grad)[0]                       # (bytes, w, h) -> bytes


def test_compose_shape_and_band():
    base = _base_jpeg(1600, 900)
    out, ow, oh = compose_share_jpeg(base, "M42", "120 s × 30 · Jul 22 2026")
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and im.mode == "RGB"
    assert ow == 1080                              # downscaled to phone width
    scaled_h = round(900 * 1080 / 1600)            # 608
    assert oh > scaled_h                           # caption band added
    assert (im.width, im.height) == (ow, oh)


def test_compose_no_upscale():
    base = _base_jpeg(320, 240)                    # narrower than max_width
    _, ow, _ = compose_share_jpeg(base, "x", "y")
    assert ow == 320


def test_compose_draws_text_on_black():
    # black photo -> any bright pixels in the band prove the caption was drawn
    black = to_jpeg(np.zeros((200, 600), dtype=np.uint16))[0]
    out, ow, oh = compose_share_jpeg(black, "TITLE", "detail line", max_width=600)
    a = np.asarray(Image.open(io.BytesIO(out)).convert("L"))
    band = a[round(200 * 600 / 600):, :]           # rows below the photo
    assert band.max() > 120                         # text pixels present


def test_compose_deterministic():
    base = _base_jpeg(800, 600)
    assert compose_share_jpeg(base, "M42", "d")[0] == compose_share_jpeg(base, "M42", "d")[0]
