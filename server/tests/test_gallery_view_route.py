"""The frame VIEWER renders to the screen, not to the grid's 256px tile.

Asked for 2026-08-19: clicking the centre of a gallery tile opens the frame at a
size appropriate to the device and orientation. The grid is pinned to 256 for
scanning; a viewer that inherited that ceiling would defeat the point of opening
it, and is precisely the confusion the fidelity test guards against.
"""
from __future__ import annotations

import io as _io
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astrodeck import gallery

CLIENT = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "frameView.ts"
REL = "NGC 7129/Light_x.fits"


@pytest.fixture
def cap(tmp_path, monkeypatch):
    import astrodeck.hub as hub_mod
    root = tmp_path / "captures"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", root)
    gallery.clear_meta_cache()
    return root


def _write(cap, rel, size=3000):
    # Bigger than the widest rung under test: the renderer never UPSCALES,
    # so a small fixture silently proves nothing about the ceiling.
    from astropy.io import fits
    p = cap / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    data = (rng.normal(400, 5, (size * 2 // 3, size))).astype(np.uint16)
    fits.PrimaryHDU(data).writeto(p, overwrite=True)
    return p


def test_the_viewer_is_not_capped_at_the_tile_width(cap):
    _write(cap, REL)
    jpeg = gallery.view(REL, width=1280)
    assert Image.open(_io.BytesIO(jpeg)).width == 1280, (
        "the viewer came back at the grid tile's size — someone opened a frame "
        "to look at it and got a scanning thumbnail")


def test_a_request_is_rounded_up_to_a_rung(cap):
    _write(cap, REL)
    # a portrait phone at 412 CSS x DPR 2.6 wants 1071
    assert Image.open(_io.BytesIO(gallery.view(REL, width=1071))).width == 1280


def test_rotation_lands_on_a_different_rung(cap):
    """The whole point of re-fetching on rotation: the two orientations must not
    round to the same rung, or the feature is a no-op."""
    portrait = gallery.view_width_for(1071)      # 412 css * 2.6
    landscape = gallery.view_width_for(1607)     # 618 css * 2.6, fitted by height
    assert portrait != landscape, (portrait, landscape)


def test_the_ceiling_holds_and_is_not_the_tile_ceiling(cap):
    _write(cap, REL, size=4000)
    assert Image.open(_io.BytesIO(gallery.view(REL, width=99999))).width == gallery.VIEW_MAX_WIDTH
    assert gallery.VIEW_MAX_WIDTH > gallery.THUMB_MAX_WIDTH


def test_the_client_ladder_matches_the_server():
    src = CLIENT.read_text(encoding="utf-8")
    m = re.search(r"VIEW_WIDTH_STEPS\s*=\s*\[([^\]]+)\]", src)
    assert m, "VIEW_WIDTH_STEPS missing from the client"
    assert [int(x) for x in re.findall(r"\d+", m.group(1))] == list(gallery.VIEW_WIDTH_STEPS)


def test_the_viewer_shares_the_tile_cache_without_colliding(cap):
    """One cache, keyed by width — a 256 tile and a 1280 view of the same frame
    must not overwrite each other."""
    _write(cap, REL)
    gallery.thumbnail(REL, width=256)
    gallery.view(REL, width=1280)
    assert gallery.thumb_is_cached(REL, 256) and gallery.thumb_is_cached(REL, 1280)
    assert Image.open(_io.BytesIO(gallery.thumbnail(REL, width=256))).width == 256
