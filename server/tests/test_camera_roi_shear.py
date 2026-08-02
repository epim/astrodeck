"""What a row-length disagreement does to a real photograph of the sky.

#110 was reported as "distorted and stretched and shown at an angle. And tiled"
on the Capture preview while the FITS from the same exposure was clean. Two
diagnoses were published and both were retracted (a binning stride guess,
disproved on the real camera; a Pillow ``mode="L"`` reinterpretation, retracted
in ddf1995 after the output bytes were measured identical either way). A third
candidate — the download being laid out at a row length the sensor did not
apply — was written down but never SHOWN to produce that picture.

WHERE THIS LEAVES #110, first, because a headline that has to be walked back is
how the previous two diagnoses did their damage:

* this mechanism DOES produce a picture that matches the words. Shown below,
  rendered through the real preview encoder, from a real sky frame.
* the mechanism is REAL on this rig, which the first version of this module
  could only assume. Player One's ``POASetImageSize`` quantizes the requested
  width down to a multiple of 4 and the height down to a multiple of 2 and
  returns POA_OK -- two instructions read out of the vendored DLL and cited by
  address at ``PlayerOneSdk.ALIGN_W``. The Poseidon-M Pro is 6252x4176, so a
  full-frame bin-2 exposure asks for 3126 px per row and gets 3124; bin 2 is
  what autofocus uses by default and what plate solve and rotate-to-PA use on
  every run. The end-to-end reproduction is in test_camera_roi_readback.py.
* it CONTRADICTS the other half of the report. ``_shape`` returns ONE array and
  the preview and ``save_fits`` both encode that object, so a shear from this
  mechanism is in the FITS too (``test_the_fits_would_carry_the_same_damage``).
  A clean FITS from the SAME exposure rules this mechanism out outright.
* so it survives only under an assumption nobody has checked: that the clean
  FITS and the sheared preview were different exposures. That is now a narrower
  thing to check than it was: the quantization bites at bin 2 and bin 4 and NOT
  at bin 1 or bin 3, so a bin-1 save (6252, untouched) sitting beside a bin-2
  preview is precisely the pairing it predicts. But "fits the account" is still
  the standard the two retracted diagnoses also met, and which exposure the
  reporter opened is not in the report.
* nor does any ONE mismatch produce all four words. "At an angle" needs a small
  width deficit; "stretched ... and tiled" needs the applied width to be about
  half the requested one. One exposure cannot be both
  (``test_no_single_mismatch_produces_all_four_of_the_reported_words``). The
  measured deficit at bin 2 is 2 px per row, which is the first case and not
  the second, so "tiled" is still unexplained by anything measured so far.

The missing measurement is therefore no longer the SDK's rule -- that one is
settled, and offline. It is which exposure the clean FITS came from, and #110
stays open on that alone.

WHAT IS ACTUALLY PROVEN HERE. Every array below comes out of the real stack —
``AsiCameraAdapter`` with its geometry read-back disabled (the adapter exactly
as it shipped on 2026-07-31) driven by ``NativeCamera.expose``, over a fake SDK
whose sensor is the committed 1024x1024 clear-sky frame from
tests/fixtures/star_noise (a real Milky Way field near Deneb, ~200 stars at HFR
3.8). Nothing here re-implements ``engine._shape``; if the engine's layout
changes, these assertions change with it, which is the only reason an evidence
module is worth keeping.

Running this module as a script rewrites the rendered comparison in
tests/fixtures/roi_shear/ so the next person can look at the picture instead of
taking the arithmetic on trust:

    cd server && .venv/Scripts/python.exe tests/test_camera_roi_shear.py

One direction is fixed, and it is worth knowing: a sensor that rounded a width
UP would need more bytes than the adapter allocated, and the SDK fails a
too-small buffer outright. So only a rounded-DOWN width can do this quietly,
and it only ever produces a frame wider than its own rows.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astrodeck.devices.cameras.adapter import ROI
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.devices.cameras.zwo_asi import AsiCameraAdapter
from astrodeck.imaging.processing import to_png

from test_camera_roi_readback import FakeRoundingAsiSdk

FIXTURES = Path(__file__).parent / "fixtures" / "star_noise"
EVIDENCE = Path(__file__).parent / "fixtures" / "roi_shear"


@pytest.fixture(autouse=True)
def _keep_the_bus_clean(monkeypatch):
    """No test in this module may write to the process-global event bus.

    ``bus._history`` is a ``deque(maxlen=200)`` shared by the whole test
    process, and several suites read new lines as ``bus.log_history[at:]`` with
    ``at = len(bus.log_history)``. Once the ring is at its cap that slice is
    empty forever, so a module that leaves real entries behind fails OTHER
    modules' tests — which is exactly what the first version of this slice did
    to test_guide_preview_source.py."""
    from astrodeck import events
    monkeypatch.setattr(events.bus, "log",
                        lambda level, message, source="hub": None)


def _sky() -> np.ndarray:
    return np.load(FIXTURES / "star_field.npz")["data"]


class _SkySdk(FakeRoundingAsiSdk):
    """A sensor whose pixels are the committed sky frame and which reads out at
    a geometry of its own choosing rather than the one it was asked for.

    ``_image`` is the shape of both vendor downloads: ASIGetDataAfterExp and
    POAGetImageData take OUR buffer's size as an argument and fail only when it
    is too SMALL, so a sensor that applied less than we asked for succeeds,
    fills the front and leaves the surplus as allocated. That is why the byte
    count never disagrees with the request."""

    def __init__(self, sky: np.ndarray, applied_w: int, applied_h: int):
        super().__init__(sensor=(sky.shape[1], sky.shape[0]))
        self._sky = sky
        self._aw, self._ah = applied_w, applied_h
        self.applied_w, self.applied_h = applied_w, applied_h

    def _apply(self, w, h, b, fmt):
        self.applied_w, self.applied_h, self.applied_bin = self._aw, self._ah, b
        if self.fmt is None:
            self.fmt = fmt

    def _image(self, nbytes: int) -> bytes:
        real = self._sky[: self._ah, : self._aw]
        buf = np.zeros(nbytes // 2, dtype="<u2")
        n = min(real.size, buf.size)
        buf[:n] = real.ravel()[:n]
        return buf.tobytes()


@dataclass(frozen=True)
class Shot:
    """One exposure's worth of the real stack's output."""
    data: np.ndarray     #: what the preview encoder and save_fits both receive
    nbytes: int          #: the size the adapter allocated for the download
    raw_len: int         #: what read_frame actually handed the engine


def previewed(applied_w: int, applied_h: int,
              req_w: int = 1024, req_h: int = 1024) -> Shot:
    """Ask a camera for ``req_w x req_h``, let it apply ``applied_w x
    applied_h``, and return what the user's Capture preview is drawn from.

    This is the shipped ZWO adapter and the shipped engine, not a model of
    them. The one modification is ``_read_back`` returning None, which is the
    adapter as it stood on the night of the report: without it the buffer size
    and the row length both come from the request, and nothing anywhere
    compares either against the sensor."""
    a = AsiCameraAdapter(sdk=_SkySdk(_sky(), applied_w, applied_h))
    a._read_back = lambda roi: None
    seen: list[int] = []
    real_read = a.read_frame

    def read_frame() -> bytes:
        raw = real_read()
        seen.append(len(raw))
        return raw

    a.read_frame = read_frame

    async def go() -> np.ndarray:
        cam = NativeCamera(a)
        await cam.connect()
        frame = await cam.expose(0.01, gain=0, offset=0,
                                 roi=ROI(x=0, y=0, w=req_w, h=req_h, bin=1))
        return frame.data

    return Shot(asyncio.run(go()), a._nbytes, seen[-1])


def test_the_byte_count_agrees_with_the_request_no_matter_what_the_sensor_did():
    """The reason the length guard in ``_shape`` cannot be the thing that
    catches this. The adapter sizes the buffer from the request, so a frame
    whose rows are the wrong length still arrives at exactly the number of
    bytes the request implies — for a sensor that applied a width 1 px short,
    8 px short, or half of what was asked for alike."""
    for applied_w, applied_h in ((1024, 1024), (1023, 1024), (1016, 1024),
                                 (512, 512), (1024, 700)):
        shot = previewed(applied_w, applied_h)
        assert shot.nbytes == 1024 * 1024 * 2
        assert shot.raw_len == 1024 * 1024 * 2
        assert shot.data.shape == (1024, 1024)


def test_a_short_applied_width_is_a_diagonal_shear_that_wraps():
    """Exactly "shown at an angle". Sensor pixel (r, c) lands at output row
    (r*applied_w + c) // req_w and column (r*applied_w + c) % req_w, so each
    successive sensor row is displaced (req_w - applied_w) pixels further left
    and the content that runs off one edge reappears on the other."""
    sky = _sky()
    req_w = req_h = 1024
    applied_w, d = 1016, 8
    out = previewed(applied_w, req_h).data

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
    out = previewed(req_w - d, req_h).data
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
    out = previewed(512, 512).data

    assert np.array_equal(out[:256, :512], sky[0:512:2, :512]), "left copy"
    assert np.array_equal(out[:256, 512:], sky[1:512:2, :512]), "right copy"
    assert not out[256:].any(), "three quarters of the frame is the empty buffer"


def test_no_single_mismatch_produces_all_four_of_the_reported_words():
    """The claim "it accounts for all four words" is assembled from two
    mutually exclusive exposures, and saying so is the difference between
    evidence and a story.

    A small width deficit gives the angle and nothing else: the field stays
    single and continuous, with only the last few rows of the buffer left
    empty. Half the width gives the tiling and nothing else: every output row
    is two WHOLE sensor rows, unsheared, over three quarters of black. A camera
    applies one width per exposure, so one frame shows one of these."""
    sky = _sky()
    angled = previewed(1016, 1024).data
    tiled = previewed(512, 512).data

    # the shear case: one continuous field, essentially no untouched buffer
    empty_tail = (1024 * 1024 - 1016 * 1024) / (1024 * 1024)
    assert empty_tail < 0.01
    assert np.mean(angled == 0) < np.mean(sky == 0) + 0.01, "nothing is tiled here"
    assert not np.array_equal(angled[:512, :512], angled[512:, :512])

    # the tiling case: no shear at all, and it is three quarters empty
    assert np.array_equal(tiled[:256, :512], sky[0:512:2, :512])
    assert np.mean(tiled == 0) > 0.7, "and no angle: just two copies over black"


def test_what_the_preview_actually_draws_for_the_tiling_case_is_a_blown_out_band():
    """Panel D of the committed sheet, and the reason panel E exists at all.

    Put the half-width frame through the REAL preview encoder and the two
    copies of the sky are NOT what a user sees: ``auto_stretch`` takes its black
    point from a frame that is three-quarters empty buffer, so the quarter that
    holds the data comes back saturated. Panel E re-stretches that band on
    itself to make the tiling legible — a second stretch the product does not
    perform. Anyone holding the sheet against the #110 screenshot has to know
    which panel is a preview and which is a diagnostic."""
    px = np.asarray(_render(previewed(512, 512).data, 620))
    band = px[: px.shape[0] // 4]
    assert (band >= 240).mean() > 0.95, (
        "panel D is supposed to be a saturated band with the tiling invisible; "
        "if structure is legible here the auto-stretch changed and the sheet's "
        "caveat about panel E is now wrong")
    assert px[px.shape[0] // 2:].max() == 0, "and black below the data"

    honest = np.asarray(_render(previewed(1024, 1024).data, 620))
    assert honest.mean() < band.mean() / 2, (
        "the same encoder leaves an undamaged sky dark, so it is the black point "
        "taken from a 3/4-empty buffer that blows D out, not the pixels")


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
    damaged = previewed(1016, 1024).data
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
    n, pad, lab, foot = PANEL_COUNT, PANEL_PAD, PANEL_LABEL, PANEL_FOOT
    with Image.open(EVIDENCE / "shear_comparison.png") as im:
        sheet = np.asarray(im.convert("L"))
    cw = (sheet.shape[1] - pad * (n + 1)) // n
    ch = sheet.shape[0] - lab - foot - pad * 2
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


PANEL_COUNT, PANEL_PAD, PANEL_LABEL, PANEL_FOOT = 5, 10, 20, 46

#: printed under the panels, because the sheet gets looked at on its own and
#: every one of these was a review finding against the first version of it.
CAVEATS = [
    "B/C and D/E are DIFFERENT mismatches. A camera applies one width per exposure, so no single frame shows all four reported words.",
    "D is what the preview actually draws for the half-width case: auto_stretch takes its black point from a 3/4-empty buffer and blows the band out.",
    "E re-stretches D's top quarter to make the tiling legible - a second stretch the product never performs. This is a mechanism, not a finding about the night: the same array goes to the FITS, so a CLEAN FITS from the SAME exposure rules this out.",
    "The mechanism is real: Player One's POASetImageSize rounds a width down to a multiple of 4 and reports success, so the 6252px Poseidon at bin 2 asks for 3126 and gets 3124. What that produces is B's lean at 2px/row - NOT D. Nothing measured yet produces the tiling.",
]


def build_comparison(cell: int = 620) -> Image.Image:
    from PIL import ImageDraw
    # (label, applied_w, applied_h, rows to render)
    cases = [
        ("A  applied width == 1024 (what we asked for)", 1024, 1024, None),
        ("B  applied 1023: one wrap, a bin-2 loop's shear to scale", 1023, 1024, None),
        ("C  applied 1016: eight wraps", 1016, 1024, None),
        ("D  applied 512x512, AS PREVIEWED: a blown-out band", 512, 512, None),
        ("E  D's top quarter RE-STRETCHED (not a preview): the sky twice", 512, 512, 256),
    ]
    panels = []
    for title, aw, ah, crop in cases:
        arr = previewed(aw, ah).data
        panels.append((title, _render(arr if crop is None else arr[:crop], cell)))
    pad, lab, foot = PANEL_PAD, PANEL_LABEL, PANEL_FOOT
    cw = max(p[1].width for p in panels)
    ch = max(p[1].height for p in panels)
    sheet = Image.new("L", (cw * len(panels) + pad * (len(panels) + 1),
                            ch + lab + foot + pad * 2), 25)
    d = ImageDraw.Draw(sheet)
    x = pad
    for title, im in panels:
        sheet.paste(im, (x, lab + pad))
        d.text((x, 5), title, fill=255)
        x += cw + pad
    y = lab + pad + ch + 6
    for line in CAVEATS:
        d.text((pad, y), line, fill=200)
        y += 12
    return sheet


if __name__ == "__main__":
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / "shear_comparison.png"
    build_comparison().save(out, optimize=True)
    print("wrote", out)
