"""Every width a client can ASK for must be a width we WARM.

Reported from the rig 2026-08-19: the gallery on a phone showed placeholder
tiles and took seconds per image, while the same gallery on a desktop was
instant. Measured through the relay:

    w=256 (desktop, DPR 1)   0.22 s    3-5 KB    warmed
    w=768 (phone,  DPR 2.6)  2.30 s   28-46 KB   NOT warmed -> full re-render

`PRECOMPUTE_WIDTHS` was (256, 512) and its comment justified the omission:
"a 1x desktop asks 256, a 2x desktop asks 512, and those two cover every tile
the grid actually draws". They do not. `THUMB_WIDTH_STEPS` in the client offers
256/384/512/768, and any phone above DPR 2 lands on 768 — so the device most
likely to be standing next to the telescope is the one that misses the cache on
every single tile, re-reading a 50 MB FITS each time.

The same comment worried that warming more widths would "triple the work". The
docstring of `precompute` immediately below says otherwise: ONE READ AND ONE
STRETCH FOR ALL THE WIDTHS, with auto_stretch measured at 1.44 s of the 1.49 s.
Extra widths are a resize and a JPEG encode.
"""
from __future__ import annotations

import re
from pathlib import Path

from astrodeck.gallery import PRECOMPUTE_WIDTHS, THUMB_MAX_WIDTH

CLIENT = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "gallery.ts"


def _client_steps() -> list[int]:
    src = CLIENT.read_text(encoding="utf-8")
    m = re.search(r"THUMB_WIDTH_STEPS\s*=\s*\[([^\]]+)\]", src)
    assert m, "THUMB_WIDTH_STEPS not found — did the client move?"
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


def test_every_width_the_client_can_request_is_warmed():
    missing = [w for w in _client_steps() if w not in PRECOMPUTE_WIDTHS]
    assert not missing, (
        f"the client can request {missing} but they are never pre-warmed, so "
        "every tile at that width re-reads the FITS and re-stretches 26 "
        "megapixels — 2.3s each, measured on the rig")


def test_the_client_cannot_ask_for_more_than_the_route_will_render():
    too_big = [w for w in _client_steps() if w > THUMB_MAX_WIDTH]
    assert not too_big, f"client asks for {too_big}, route clamps at {THUMB_MAX_WIDTH}"


def test_we_do_not_warm_widths_nobody_asks_for():
    """The cost is disk, and the point is to match the client, not to guess."""
    extra = [w for w in PRECOMPUTE_WIDTHS if w not in _client_steps()]
    assert not extra, f"warming {extra}, which no client requests"


def test_the_grid_asks_for_exactly_one_width():
    """ONE WIDTH IS THE POINT, not an accident of the current numbers.

    Chosen 2026-08-19 after comparing the same frame at 256/384/768 scaled into
    a real 445-device-pixel phone tile: near identical to the eye, at 0.5 MB
    versus 5.6 MB to scan 200 frames. A second width would reintroduce the cache
    fragmentation that made a phone re-render a 50 MB FITS per tile.
    """
    assert PRECOMPUTE_WIDTHS == (256,), PRECOMPUTE_WIDTHS
    assert _client_steps() == [256], _client_steps()


def test_the_route_clamps_a_stray_width_rather_than_rendering_it():
    """The route is reachable by anything, and a stray `w` is exactly how the
    cache fragmented before. Clamping server-side is what makes "one width"
    true rather than merely intended."""
    assert THUMB_MAX_WIDTH == 256, (
        f"the route will still render up to {THUMB_MAX_WIDTH}px, so one caller "
        "asking for more brings back the per-tile FITS re-render")
