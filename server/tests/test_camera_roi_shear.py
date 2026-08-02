"""What a row-length disagreement does to a real photograph of the sky.

#110 was reported as "distorted and stretched and shown at an angle. And tiled"
on the Capture preview while the FITS from the same exposure was clean. Two
diagnoses were published and both were retracted (a binning stride guess,
disproved on the real camera; a Pillow ``mode="L"`` reinterpretation, retracted
in ddf1995 after the output bytes were measured identical either way). A third
candidate — ``engine.py::_shape`` laying the download out at a row length the
sensor did not apply — was written down but never SHOWN to produce that picture.

This module shows it. It takes the committed 1024x1024 clear-sky frame from
tests/fixtures/star_noise (a real Milky Way field near Deneb, ~200 stars at
HFR 3.8), runs it through exactly what the adapters and the engine do —

    adapter: buffer = zeros(req_w * req_h * 2); the SDK fills the front of it
    engine:  frombuffer(buf, "<u2", count=req_w*req_h).reshape(req_h, req_w)

— and pins the result down arithmetically. Everything below is an exact
identity, not a threshold, so it says precisely which of the reported words the
mechanism explains and which it does not.

Running this module as a script rewrites the rendered comparison in
tests/fixtures/roi_shear/ so the next person can look at the picture instead of
taking the arithmetic on trust:

    cd server && .venv/Scripts/python.exe tests/test_camera_roi_shear.py

VERDICT, so it is not buried: the mechanism reproduces the report. A small
mismatch (applied width a few pixels under the requested one) turns every star
into a diagonal streak and rolls the field around a diagonal seam — "at an
angle". A large one (the camera at twice the bin we asked for) puts two copies
of the sky side by side, vertically squeezed, over a black lower frame —
"stretched" and "tiled". Panel B of the committed sheet is the night's own
numbers to scale: the imaging sensor is 6252 wide, a bin-2 loop asks for 3126,
and 3126 is not a multiple of 4.

What this does NOT establish is that it is what happened. The one number that
settles it is the width the Player One SDK actually applied for that exposure,
and reading it needs the camera. Two things to hold on to while doing that:

* a shear from this mechanism is in the FITS as well as the preview — the same
  array goes to both (test_the_fits_would_carry_the_same_damage...). The report
  of a clean FITS beside a sheared preview is only consistent if they came from
  different exposures, which fits the rest of the account: the frames SAVED that
  night were bin 1 (6252, a multiple of 4) and the sheared preview came from a
  bin-2 loop (3126, not).
* the direction is fixed. A sensor that rounded a width UP would need more bytes
  than the adapter allocated, and the SDK fails a too-small buffer outright. So
  only a rounded-DOWN width can do this quietly, and only ever produces a frame
  wider than its own rows.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astrodeck.imaging.processing import to_png

FIXTURES = Path(__file__).parent / "fixtures" / "star_noise"
EVIDENCE = Path(__file__).parent / "fixtures" / "roi_shear"


def _sky() -> np.ndarray:
    return np.load(FIXTURES / "star_field.npz")["data"]


def download(sensor: np.ndarray, applied_w: int, applied_h: int,
             req_w: int, req_h: int) -> bytes:
    """The bytes ``read_frame`` returns when the sensor applied a geometry we
    did not ask for: OUR buffer, sized from the REQUEST, with the sensor's own
    rows packed into the front of it and the tail left as allocated.

    This is the shape of both vendor downloads. ASIGetDataAfterExp and
    POAGetImageData take the buffer size as an argument and fail only when it is
    too SMALL, so a sensor that applied less than we asked for succeeds and
    leaves the surplus untouched — which is why the byte count never disagrees.
    """
    real = sensor[:applied_h, :applied_w]
    buf = np.zeros(req_w * req_h, dtype="<u2")
    buf[: real.size] = real.ravel()
    return buf.tobytes()


def shape(raw: bytes, req_w: int, req_h: int) -> np.ndarray:
    """engine.py::_shape's layout step, verbatim."""
    return np.frombuffer(raw, dtype="<u2", count=req_w * req_h).reshape(req_h, req_w)


def test_the_byte_count_agrees_with_the_request_no_matter_what_the_sensor_did():
    """The reason the length guard in _shape cannot be the thing that catches
    this. The adapter sizes the buffer from the request, so a frame whose rows
    are the wrong length still arrives at exactly the right number of bytes."""
    sky = _sky()
    req_w = req_h = 1024
    for applied_w, applied_h in ((1024, 1024), (1016, 1024), (512, 512), (1024, 700)):
        raw = download(sky, applied_w, applied_h, req_w, req_h)
        assert len(raw) == req_w * req_h * 2


def test_a_short_applied_width_is_a_diagonal_shear_that_wraps():
    """Exactly "shown at an angle". Sensor pixel (r, c) lands at output row
    (r*applied_w + c) // req_w and column (r*applied_w + c) % req_w, so each
    successive sensor row is displaced (req_w - applied_w) pixels further left
    and the content that runs off one edge reappears on the other."""
    sky = _sky()
    req_w = req_h = 1024
    applied_w, d = 1016, 8
    out = shape(download(sky, applied_w, req_h, req_w, req_h), req_w, req_h)

    rows = min(req_h, (req_w * req_h) // applied_w)
    idx = np.arange(rows * applied_w)
    expect = sky[:rows, :applied_w].ravel()
    got = out.ravel()[: idx.size]
    assert np.array_equal(got, expect), "the layout is not a pure re-cut of the rows"

    # and the displacement really is d pixels per sensor row, wrapping
    for r in (0, 1, 2, 50, 200):
        lin = r * applied_w
        assert out[lin // req_w, lin % req_w] == sky[r, 0]
        assert (lin % req_w) == (-r * d) % req_w


def test_a_star_becomes_a_streak_leaning_the_same_way_in_every_case():
    """The human-visible consequence: a round point source is spread across
    (rows it spans) x (pixels lost per row) columns, always leaning the same
    way, which is why the whole frame reads as combed rather than blurred."""
    sky = _sky()
    req_w = req_h = 1024
    d = 8
    inner = sky[64:-64, 64:-64]
    r0, c0 = np.unravel_index(int(np.argmax(inner)), inner.shape)
    r0, c0 = int(r0) + 64, int(c0) + 64

    def extent(a, y, x, half=32):
        win = a[y - half:y + half + 1, x - half:x + half + 1].astype(float)
        ys, xs = np.nonzero(win >= win.max() * 0.5)
        return int(ys.max() - ys.min()) + 1, int(xs.max() - xs.min()) + 1

    tall, wide = extent(sky, r0, c0)
    out = shape(download(sky, req_w - d, req_h, req_w, req_h), req_w, req_h)
    lin = r0 * (req_w - d) + c0
    sheared = extent(out, lin // req_w, lin % req_w)

    assert wide <= 20, "the source is compact in the frame the sensor produced"
    assert sheared[1] >= wide + d * (tall - 1), (
        f"{tall}-row source lost {d} px a row and must span at least "
        f"{wide + d * (tall - 1)} columns, got {sheared[1]}")


def test_half_the_applied_width_puts_two_copies_of_the_sky_side_by_side():
    """Exactly "stretched ... and tiled", and the case a stale bin produces: ask
    for bin 2 while the camera is still at bin 4 and every output row holds two
    consecutive sensor rows, so the field appears twice across, squeezed to half
    height, over a frame that is black below it."""
    sky = _sky()
    req_w = req_h = 1024
    out = shape(download(sky, 512, 512, req_w, req_h), req_w, req_h)

    assert np.array_equal(out[:256, :512], sky[0:512:2, :512]), "left copy"
    assert np.array_equal(out[:256, 512:], sky[1:512:2, :512]), "right copy"
    assert not out[256:].any(), "three quarters of the frame is the empty buffer"


def test_the_fits_would_carry_the_same_damage_so_the_night_is_not_closed(tmp_path):
    """The one thing this mechanism does NOT explain, kept where it cannot be
    lost. ``_shape`` returns ONE array; ``save_fits`` writes ``frame.data`` and
    the preview encodes the same object, with no second layout step in which one
    could recover and the other not. So a shear from this mechanism is in the
    FITS too — demonstrated here by writing one and reading it back.

    The report was that the saved FITS was CLEAN. Both cannot be true of ONE
    frame, so this mechanism requires that the clean FITS and the sheared
    preview were different exposures — which is what a bin-1 save alongside a
    bin-2 preview loop would be. Anyone closing #110 has to confirm that
    pairing; a clean FITS from the SAME exposure would rule this mechanism out
    entirely, and that check needs the rig."""
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits

    sky = _sky()
    damaged = shape(download(sky, 1016, 1024, 1024, 1024), 1024, 1024)
    assert not np.array_equal(damaged, sky), "the damage is in the array itself"

    frame = CameraFrame(data=damaged, exposure_s=4.0, gain=220, offset=10,
                        binning=1, bayer_pattern=None, temperature_c=-10.0,
                        timestamp=0.0)
    path = tmp_path / "sheared.fits"
    save_fits(frame, path)

    from astropy.io import fits
    with fits.open(path) as hdul:
        assert np.array_equal(hdul[0].data, damaged), (
            "the FITS carries the shear — a clean FITS beside a sheared preview "
            "is NOT what this mechanism produces")


@pytest.mark.skipif(not (EVIDENCE / "shear_comparison.png").exists(),
                    reason="rendered comparison not committed")
def test_the_committed_comparison_actually_shows_different_pictures():
    """A comparison sheet is evidence only if the panels differ. This catches
    the way an evidence render goes bad: a generator bug that draws the same
    frame in every cell, which would look convincing and prove nothing."""
    n, pad, lab = PANEL_COUNT, PANEL_PAD, PANEL_LABEL
    with Image.open(EVIDENCE / "shear_comparison.png") as im:
        sheet = np.asarray(im.convert("L"))
    cw = (sheet.shape[1] - pad * (n + 1)) // n
    ch = sheet.shape[0] - lab - pad * 2
    panels = [sheet[lab + pad: lab + pad + ch,
                    pad + i * (cw + pad): pad + i * (cw + pad) + cw]
              for i in range(n)]
    for i in range(1, n):
        assert not np.array_equal(panels[0], panels[i]), \
            f"panel {i} is identical to the undamaged panel A"
    # panel D is the half-width case: its lower half is the untouched buffer
    assert panels[3][-ch // 3:].max() <= 8, \
        "panel D should be black below the data — the sheet is not the D case"


# --------------------------------------------------------------------------
# regeneration: `python tests/test_camera_roi_shear.py`
# --------------------------------------------------------------------------
def _render(arr: np.ndarray, max_width: int) -> Image.Image:
    """Through the REAL preview encoder, so the sheet is what the user saw and
    not a matplotlib impression of it."""
    return Image.open(io.BytesIO(to_png(arr, stretch=True,
                                        max_width=max_width))).convert("L")


PANEL_COUNT, PANEL_PAD, PANEL_LABEL = 5, 10, 20


def build_comparison(cell: int = 620) -> Image.Image:
    from PIL import ImageDraw
    sky = _sky()
    h, w = sky.shape
    # (label, applied_w, applied_h, rows to render) — the last panel re-stretches
    # the band panel D blows out, because the auto-stretch takes its black point
    # from a frame that is three-quarters empty buffer and the tiling inside the
    # band is what the eye needs to see.
    cases = [
        ("A  applied width == 1024 (what we asked for)", 1024, h, None),
        ("B  applied 1023: one wrap, the night's shear to scale", 1023, h, None),
        ("C  applied 1016: eight wraps", 1016, h, None),
        ("D  applied 512x512 (camera at twice our bin), as previewed", 512, 512, None),
        ("E  the top quarter of D: the sky twice, side by side", 512, 512, 256),
    ]
    panels = []
    for title, aw, ah, crop in cases:
        arr = shape(download(sky, aw, ah, w, h), w, h)
        panels.append((title, _render(arr if crop is None else arr[:crop], cell)))
    pad, lab = PANEL_PAD, PANEL_LABEL
    cw = max(p[1].width for p in panels)
    ch = max(p[1].height for p in panels)
    sheet = Image.new("L", (cw * len(panels) + pad * (len(panels) + 1),
                            ch + lab + pad * 2), 25)
    d = ImageDraw.Draw(sheet)
    x = pad
    for title, im in panels:
        sheet.paste(im, (x, lab + pad))
        d.text((x, 5), title, fill=255)
        x += cw + pad
    return sheet


if __name__ == "__main__":
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / "shear_comparison.png"
    build_comparison().save(out, optimize=True)
    print("wrote", out)
