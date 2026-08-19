"""The gallery's 256px tile must never become the preview's ceiling.

2026-08-19: the gallery grid was pinned to one 256px width after the operator
compared sizes in a real phone tile. That judgement was scoped to the SCANNING
GRID. Capture previews — the thing an operator actually judges focus from — must
stay high fidelity, and they take a different path.

This test exists because the two are one `import` away from being confused, and
the failure would be silent: previews would simply get soft and nobody would get
an error.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from astrodeck import gallery
from astrodeck.imaging import processing

SERVER = Path(__file__).resolve().parents[1] / "astrodeck"


def test_the_gallery_clamp_is_confined_to_the_gallery_module():
    """`THUMB_MAX_WIDTH` is 256. If anything outside gallery.py reads it, that
    module has just inherited a 256px ceiling it never asked for."""
    hits = []
    for py in SERVER.rglob("*.py"):
        if py.name == "gallery.py":
            continue
        if "THUMB_MAX_WIDTH" in py.read_text(encoding="utf-8"):
            hits.append(py.relative_to(SERVER).as_posix())
    assert not hits, (
        f"{hits} reference the GALLERY TILE clamp ({gallery.THUMB_MAX_WIDTH}px). "
        "Capture previews must not be sized by it")


def test_EVERY_preview_encoder_still_defaults_wide():
    """The live preview is the operator's working view; 1400px is its floor of
    usefulness, and it is not a thumbnail.

    BOTH encoders, checked by name rather than by picking one: an earlier
    version of this test inspected only `to_jpeg`, and a sabotage that shrank
    `to_png` — the encoder behind the lossless, render and crop routes — sailed
    straight past it."""
    checked = []
    for name in ("to_jpeg", "to_png"):
        fn = getattr(processing, name)
        param = inspect.signature(fn).parameters.get("max_width")
        assert param is not None, f"{name} lost its max_width parameter"
        if param.default is inspect.Parameter.empty:
            continue                       # caller-supplied, e.g. the 1:1 crop
        checked.append(name)
        assert param.default >= 1400, (
            f"{name} defaults to {param.default}px — previews are judged for "
            "focus and cannot inherit a grid tile's size")
    assert checked, "neither encoder had a default to check — signatures moved"


def test_the_pixel_peep_crop_is_not_downscaled():
    """`/api/preview/{id}/crop` promises sensor 1:1. It passes the crop's own
    width as max_width so `_encode` never shrinks it — that is the whole point
    of a pixel-peep, and a clamp anywhere in this path would silently break the
    one view that answers "is this actually in focus"."""
    src = (SERVER / "api" / "app.py").read_text(encoding="utf-8")
    m = re.search(r"async def preview_crop\(.*?return Response", src, re.S)
    assert m, "preview_crop not found — did it move?"
    body = m.group(0)
    assert "crop.shape[1]" in body, (
        "the 1:1 crop no longer sizes itself from the crop width; something is "
        "imposing a max width on a sensor-1:1 view")
    assert "THUMB_MAX_WIDTH" not in body
