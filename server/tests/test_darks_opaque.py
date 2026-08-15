"""A dark shot through the "opaque" slot must be checked, not believed.

The rig case (2026-08-01): the slot the operator ticked as blackout was empty,
so 1s/4s/10s "darks" came back median 65535 max 65535 and were filed as a dark
library. The trap in fixing that is ``max``: the committed REAL overcast-sky
frame has max 65535 too, from a single hot pixel. Every rejection here keys off
the frame's bulk or off resolved sources, never off its brightest pixel.

Fixtures: the two ``star_noise`` .npz files are REAL frames off this rig (a
fully-clouded sub and a Milky Way field) and carry the two cases where real
pixel statistics are the whole point — the hot pixel that must not condemn a
frame, and the star field that must. Everything else is synthetic, because
daylight through an empty slot, amp glow, hot columns, cosmic-ray tracks and a
railed 12-bit readout are conditions we deliberately never reproduce on
hardware.
"""
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging.darks import (
    DarkResult,
    judge_dark,
    opaque_claim_refuted,
)
from astrodeck.imaging.stars import detect_stars

FIXTURES = Path(__file__).parent / "fixtures" / "star_noise"


def real_frame(name: str) -> np.ndarray:
    return np.load(FIXTURES / f"{name}.npz")["data"]


def bias_frame(level: float = 264.0, sigma: float = 22.0,
               shape: tuple[int, int] = (1024, 1024), seed: int = 3) -> np.ndarray:
    """A plausible dark: a bias pedestal plus read noise, nothing else."""
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(level, sigma, shape), 0, 65535).astype(np.uint16)


# --------------------------------------------------------------- the defect

def test_a_frame_pinned_at_full_well_is_not_a_dark():
    """The rig case verbatim: median 65535, max 65535."""
    frame = np.full((1024, 1024), 65535, dtype=np.uint16)
    r = judge_dark(frame)
    assert isinstance(r, DarkResult)
    assert not r.is_dark
    assert r.verdict == "saturated"
    assert r.level == 65535.0 and r.saturated_frac == 1.0


@pytest.mark.parametrize("clipped_frac", [1.0, 0.92, 0.30])
def test_a_mostly_clipped_frame_is_rejected_however_much_survives(clipped_frac):
    """The 1s frame need not pin every pixel the way the 10s one does. The
    verdict must not hinge on the survivors — a third of the frame at full well
    is already a frame that cannot calibrate anything."""
    rng = np.random.default_rng(41)
    frame = np.clip(rng.normal(40000, 1500, (512, 512)), 0, 65535).astype(np.uint16)
    frame[rng.random(frame.shape) < clipped_frac] = 65535
    r = judge_dark(frame, full_well=65535)
    assert not r.is_dark
    assert r.verdict == "saturated"
    assert "65535" in r.reason


def test_the_rejection_sentence_names_the_measured_numbers():
    """The reason goes in front of the operator unchanged, so it has to carry
    the evidence — "that is not a dark" with no ADU value is the unfalsifiable
    claim this module replaces."""
    r = judge_dark(np.full((256, 256), 65535, dtype=np.uint16))
    assert "65535" in r.reason
    assert "100%" in r.reason
    assert "not a dark" in r.reason
    assert "light is reaching the sensor" in r.reason.lower()


def test_a_daylight_leak_below_saturation_is_still_not_a_dark():
    """The same empty slot an hour before sunset: flooded, nothing clipped."""
    rng = np.random.default_rng(9)
    frame = np.clip(rng.normal(26000, 900, (512, 512)), 0, 65535).astype(np.uint16)
    r = judge_dark(frame)
    assert not r.is_dark
    assert r.verdict == "elevated"
    assert r.saturated_pixels == 0          # max never entered into it
    assert "40%" in r.reason


# ------------------------------------------ the leak that has stars in it

def test_the_real_star_field_is_rejected_outright_not_called_plausible():
    """REAL fixture, and the case the module used to declare unprovable: an
    empty slot at 2 a.m. with the scope uncapped is a STAR FIELD, and a star
    field is exactly what one frame can settle. This sub sits at 310 ADU —
    indistinguishable from a bias pedestal by level, which is why the level
    test cannot see it and the source test must."""
    r = judge_dark(real_frame("star_field"), full_well=65535, exposure_s=4)
    assert not r.is_dark
    assert r.verdict == "stars"
    assert r.level < 400                      # the level test could never fire
    assert r.sources >= 12 and r.source_zones >= 8
    assert "Dark current does not make stars" in r.reason


def test_a_leak_a_tenth_as_bright_as_the_fixture_is_still_provable():
    """The real field pushed down to a tenth of its contrast above the
    pedestal — the weakest true leak measured. It has to stay well clear of the
    bar, or the bar is calibrated to one frame rather than to a signal."""
    sf = real_frame("star_field").astype(np.float64)
    level = float(np.median(sf))
    faint = np.clip(level + (sf - level) * 0.1, 0, 65535).astype(np.uint16)
    r = judge_dark(faint, full_well=65535, exposure_s=4)
    assert not r.is_dark and r.verdict == "stars"
    assert r.sources >= 40                    # measured 68; bar is 12


def test_the_star_verdict_reuses_stars_the_caller_already_detected():
    """The capture path runs detect_stars once per frame already, so the leak
    proof has to cost nothing there — and the handed-in list must give the same
    answer as detecting here, or the two call styles disagree."""
    frame = real_frame("star_field")
    theirs = judge_dark(frame, full_well=65535, stars=detect_stars(frame))
    ours = judge_dark(frame, full_well=65535)
    assert theirs.verdict == ours.verdict == "stars"
    assert theirs.sources == ours.sources


def test_a_frame_settled_by_its_statistics_reports_sources_unmeasured():
    """A saturated frame never pays for a detection pass, and the payload says
    "not measured" rather than the zero that pass would not have produced. A
    number nobody computed presented as a measurement is this project's whole
    recurring defect."""
    r = judge_dark(np.full((512, 512), 65535, dtype=np.uint16), full_well=65535)
    assert r.verdict == "saturated"
    assert r.sources is None and r.source_zones is None
    assert r.to_dict()["sources"] is None


# ------------------------------------------- what must NOT be condemned

def test_one_saturated_hot_pixel_does_not_condemn_a_real_frame():
    """REAL fixture. blank_overcast has max 65535 — the identical max as the
    daylight leak — from exactly one hot pixel out of 1,048,576. A max-based
    check condemns it; a median-based one does not."""
    frame = real_frame("blank_overcast")
    assert int(frame.max()) == 65535, "fixture no longer carries the hot pixel"
    r = judge_dark(frame, full_well=65535, exposure_s=8)
    assert r.is_dark
    assert r.saturated_pixels == 1
    assert r.peak == 65535.0 and r.level < 300
    assert r.sources == 0                     # and no star was invented either


def test_scattered_hot_pixels_do_not_condemn_the_frame():
    """500 saturated pixels (0.05%) is a tired sensor, not a light leak."""
    frame = bias_frame()
    rng = np.random.default_rng(17)
    ys = rng.integers(0, frame.shape[0], 500)
    xs = rng.integers(0, frame.shape[1], 500)
    frame[ys, xs] = 65535
    r = judge_dark(frame, full_well=65535)
    assert r.is_dark
    assert r.saturated_pixels >= 490


@pytest.mark.parametrize("n_hot,value", [(5000, 65535), (5000, 20000), (5000, 3000)])
def test_a_sensor_thick_with_hot_pixels_is_not_a_star_field(n_hot, value):
    """5000 hot pixels in a megapixel is 0.48% — under the clipped-fraction bar,
    and detect_stars finds ~96 "stars" in them because random pairs land
    adjacent and a pair shares flux the way a PSF does. What a pair does NOT do
    is spread: measured flux-over-peak-excess 2-6 px against a real star's 31.
    Run at three brightnesses so the saturation of the pixels is not what is
    doing the work."""
    frame = bias_frame()
    rng = np.random.default_rng(19)
    frame[rng.integers(0, 1024, n_hot), rng.integers(0, 1024, n_hot)] = value
    r = judge_dark(frame, full_well=65535, exposure_s=600)
    assert r.is_dark, r.reason
    assert len(detect_stars(frame)) > 50      # the detector IS fooled
    assert r.sources == 0                     # this module is not


def test_amp_glow_in_one_corner_does_not_condemn_the_frame():
    """A 72x72 saturated corner is 0.49% of a 1024x1024 frame — under the bar,
    and it cannot move the median at all."""
    frame = bias_frame()
    frame[:72, :72] = 65535
    r = judge_dark(frame, full_well=65535)
    assert r.is_dark
    assert r.saturated_frac < 0.005
    assert r.level < 300                      # the corner never touched the bulk


@pytest.mark.parametrize("amp", [2000, 8000, 40000])
def test_a_bright_amp_glow_gradient_is_not_a_star_field(amp):
    """The glow that is NOT a saturated block is the dangerous one: its own
    shot noise peaks all over the bright corner and detect_stars returns up to
    105 sources there. They fail on spread (a noise peak is tight) and they
    fail on coverage — a glow is one corner, and sky is the whole frame."""
    yy, xx = np.mgrid[0:1024, 0:1024]
    glow = amp * np.exp(-((yy ** 2 + xx ** 2) / (2 * 150.0 ** 2)))
    shot = np.random.default_rng(4).normal(0, np.sqrt(np.maximum(glow, 0)) + 1e-9)
    frame = np.clip(bias_frame().astype(np.float64) + glow + shot,
                    0, 65535).astype(np.uint16)
    r = judge_dark(frame, full_well=65535, exposure_s=600)
    assert r.is_dark, r.reason
    assert r.source_zones is not None and r.source_zones < 8


def test_hot_columns_are_not_a_star_field():
    """Five hot columns and three hot rows give detect_stars 200 detections
    that DO spread (a 15px cutout of a bright column holds 15 bright pixels).
    They are perfectly linear — eccentricity 1.00 against a real star's 0.65 —
    and that is the only thing separating them. Without the eccentricity gate
    this frame is condemned as a light leak."""
    frame = bias_frame()
    for c in (100, 300, 500, 700, 900):
        frame[:, c] = 40000
    for row in (150, 450, 750):
        frame[row, :] = 40000
    assert len(detect_stars(frame)) >= 100    # the detector IS fooled
    r = judge_dark(frame, full_well=65535, exposure_s=600)
    assert r.is_dark, r.reason
    assert r.sources < 12


def test_cosmic_ray_tracks_in_a_long_dark_are_not_a_star_field():
    """A 600 s dark on a large sensor collects tens of cosmic-ray hits, and the
    long ones are frame-wide, bright and numerous — 150 of them passed the
    spread test. They are tracks: eccentricity 0.97-0.99. A real dark condemned
    as a leak would make the check worse than useless."""
    rng = np.random.default_rng(2)
    frame = bias_frame().astype(np.float64)
    for _ in range(80):
        y, x = rng.integers(20, 1000, 2)
        frame[y:y + 2, x:x + rng.integers(3, 30)] += 25000
    frame = np.clip(frame, 0, 65535).astype(np.uint16)
    r = judge_dark(frame, full_well=65535, exposure_s=600)
    assert r.is_dark, r.reason
    assert r.sources == 0


@pytest.mark.parametrize("name,make", [
    ("banding", lambda b, yy, xx: b + 300 * np.sin(yy / 3.0)),
    ("smooth ramp", lambda b, yy, xx: b + 8000 * (xx / 1023.0)),
    ("broad reflection",
     lambda b, yy, xx: b + 2000 * np.exp(-(((yy - 512) ** 2 + (xx - 512) ** 2)
                                           / (2 * 400.0 ** 2)))),
])
def test_structured_dark_artefacts_are_not_a_star_field(name, make):
    """Read-noise banding, a readout ramp and a broad internal reflection: all
    three are structure a real dark carries, and none of them is a source."""
    yy, xx = np.mgrid[0:1024, 0:1024]
    frame = np.clip(make(bias_frame().astype(np.float64), yy, xx),
                    0, 65535).astype(np.uint16)
    r = judge_dark(frame, full_well=65535, exposure_s=600)
    assert r.is_dark, f"{name}: {r.reason}"


def test_a_warm_sensor_dark_is_not_flagged():
    """Elevated by dark current, not by light: 3% of full well."""
    r = judge_dark(bias_frame(level=2000.0, sigma=180.0), full_well=65535,
                   exposure_s=60)
    assert r.is_dark
    assert r.verdict == "dark"


def test_a_long_exposure_with_real_dark_current_is_not_flagged():
    """A 600 s dark at high gain: 14% of full well, still under the ceiling a
    600 s exposure earns."""
    r = judge_dark(bias_frame(level=9000.0, sigma=700.0), full_well=65535,
                   exposure_s=600)
    assert r.is_dark
    assert 0.1 < r.level_frac < 0.2


def test_a_quantised_low_gain_bias_is_not_mistaken_for_a_railed_readout():
    """Read noise under 1 ADU puts half the pixels at the maximum value — the
    same pile-up a rail produces. It also puts the other half at the minimum,
    which a rail never does."""
    rng = np.random.default_rng(5)
    frame = (100 + rng.integers(0, 2, (512, 512))).astype(np.uint16)
    r = judge_dark(frame, full_well=65535, exposure_s=0)
    assert r.is_dark
    assert r.verdict == "dark"


def test_the_accepted_sentence_says_plausible_not_verified():
    """One frame cannot prove no light reached the sensor, and the copy must
    not imply it did."""
    r = judge_dark(bias_frame(), full_well=65535, exposure_s=300)
    assert r.reason.startswith("plausible dark")
    assert "264" in r.reason


# ------------------------------------------ the ceiling scales with exposure

def test_a_one_second_frame_at_a_quarter_of_full_well_is_not_a_dark():
    """The hole this closes. Under one flat 25% ceiling, a 1 s frame sitting at
    15700 ADU came back "plausible dark: median 15700 ADU (24% of full well)" —
    a level no bias and no 1 s exposure reaches by dark current, and exactly
    what the empty slot produces at dusk rather than at noon. The rig's frames
    happened to be pinned at 65535 so the saturation test caught them; one stop
    weaker and only this test does."""
    frame = bias_frame(level=15700.0, sigma=300.0)
    assert judge_dark(frame, full_well=65535).is_dark          # the old blind spot
    r = judge_dark(frame, full_well=65535, exposure_s=1)
    assert not r.is_dark
    assert r.verdict == "elevated"
    assert "1 s dark" in r.reason and "24%" in r.reason


def test_the_same_level_is_accepted_once_the_exposure_can_explain_it():
    """Not a level check with a new number in it: 15700 ADU is a legitimate
    600 s dark on a hot sensor at high gain and must pass at 600 s."""
    frame = bias_frame(level=15700.0, sigma=300.0)
    assert judge_dark(frame, full_well=65535, exposure_s=600).is_dark
    assert not judge_dark(frame, full_well=65535, exposure_s=30).is_dark


@pytest.mark.parametrize("exposure_s", [0, 0.001, 1, 30, 120, 600, 3600])
def test_stating_the_exposure_can_only_tighten_the_verdict(exposure_s):
    """The ceiling must never rise above the exposure-free one, or handing the
    module a number it already had would start ACCEPTING frames it used to
    reject."""
    r = judge_dark(bias_frame(), full_well=65535, exposure_s=exposure_s)
    assert r.level_frac_limit <= judge_dark(bias_frame(),
                                            full_well=65535).level_frac_limit


def test_an_unusable_exposure_falls_back_to_the_loosest_ceiling():
    """A negative or NaN exposure is a caller that does not know, not a short
    frame. Treating it as zero would apply the strictest ceiling in the module
    to a frame nobody measured."""
    loose = judge_dark(bias_frame(), full_well=65535).level_frac_limit
    for bad in (-1.0, float("nan"), float("inf"), "20s"):
        assert judge_dark(bias_frame(), full_well=65535,
                          exposure_s=bad).level_frac_limit == loose


def test_the_elevated_sentence_names_the_ceiling_and_where_it_came_from():
    """"Too bright" is not actionable; "24% against the 6% a 1 s dark reaches"
    tells the operator both the measurement and the standard."""
    r = judge_dark(bias_frame(level=15700.0, sigma=300.0), full_well=65535,
                   exposure_s=1)
    assert "6.0%" in r.reason
    assert "bias pedestal plus dark current" in r.reason


# --------------------------------- the assertion actually under test

def test_a_rejection_names_the_blackout_slot_that_is_passing_light():
    """The point of the whole check. Without the slot, the copy reads
    identically for a cap left off and for a blackout flag on an empty slot,
    and only one of those is something the software can fix."""
    r = judge_dark(np.full((256, 256), 65535, dtype=np.uint16), full_well=65535,
                   opaque_slot=3, filter_name="Dark")
    assert 'slot 3 ("Dark")' in r.reason
    assert "flagged blackout" in r.reason
    assert r.opaque_slot == 3


def test_the_slot_sentence_stops_short_of_claiming_the_slot_is_empty():
    """Light on the sensor with that slot commanded is also what a wheel that
    never moved produces. The module knows which two, not which one."""
    r = judge_dark(np.full((256, 256), 65535, dtype=np.uint16), full_well=65535,
                   opaque_slot=0)
    assert "either that slot is not blanked or the wheel never reached it" in r.reason
    assert "slot 0" in r.reason               # unnamed slot still identified


def test_a_refuted_opaque_flag_is_reported_so_it_can_be_retracted():
    """The frames stopping is not the fix: dark_slot() returns the first
    flagged slot and the sequence engine drives every dark to it, so a flag
    that survives its own refutation routes tomorrow night's darks through the
    same empty slot."""
    saturated = judge_dark(np.full((256, 256), 65535, dtype=np.uint16),
                           full_well=65535, opaque_slot=2)
    assert opaque_claim_refuted(saturated) == 2
    starry = judge_dark(real_frame("star_field"), full_well=65535, opaque_slot=2)
    assert starry.verdict == "stars" and opaque_claim_refuted(starry) == 2


@pytest.mark.parametrize("frame,expected_verdict", [
    (np.full((64, 64), 300, dtype=np.uint16), "constant"),
    (np.zeros((0, 0), dtype=np.uint16), "empty"),
])
def test_a_broken_readout_does_not_refute_the_opaque_flag(frame, expected_verdict):
    """A buffer the camera handed back says nothing whatever about what the
    slot is made of. Retracting the flag on that evidence would replace one
    unchecked assertion with another."""
    r = judge_dark(frame, full_well=65535, opaque_slot=1)
    assert r.verdict == expected_verdict and not r.is_dark
    assert opaque_claim_refuted(r) is None


def test_a_railed_readout_does_not_refute_the_opaque_flag():
    rng = np.random.default_rng(23)
    frame = rng.integers(200, 4096, (512, 512)).astype(np.uint16)
    frame[rng.random(frame.shape) < 0.8] = 4095
    r = judge_dark(frame, opaque_slot=1)
    assert r.verdict == "clipped"
    assert opaque_claim_refuted(r) is None


def test_an_accepted_dark_refutes_nothing():
    r = judge_dark(bias_frame(), full_well=65535, exposure_s=300, opaque_slot=1)
    assert r.is_dark and opaque_claim_refuted(r) is None


# ------------------------------------- frames that are not measurements

def test_a_railed_readout_is_caught_without_camera_metadata():
    """A 12-bit sensor in a 16-bit container, MaxADU never reported: 4095 is
    6% of the container so no level test can fire, but 80% of the frame
    sitting at exactly one value can only be a rail."""
    rng = np.random.default_rng(23)
    frame = rng.integers(200, 4096, (512, 512)).astype(np.uint16)
    frame[rng.random(frame.shape) < 0.8] = 4095
    r = judge_dark(frame)                     # no full_well
    assert not r.is_dark
    assert r.verdict == "clipped"
    assert "4095" in r.reason


def test_the_cameras_own_full_well_beats_the_container_ceiling():
    """Same 12-bit frame, but the driver reported MaxADU — now it is saturation,
    and the sentence says so."""
    frame = np.full((256, 256), 4095, dtype=np.uint16)
    r = judge_dark(frame, full_well=4095)
    assert r.verdict == "saturated"
    assert r.saturation_adu == 4095.0
    assert judge_dark(frame).verdict != "saturated"   # without it: a rail


def test_a_constant_frame_is_not_an_exposure():
    """Every pixel identical at a plausible bias level: no sensor with read
    noise produces that, so it is a buffer the camera handed back, not a dark."""
    r = judge_dark(np.full((256, 256), 300, dtype=np.uint16), full_well=65535)
    assert not r.is_dark
    assert r.verdict == "constant"
    assert "300" in r.reason


def test_an_empty_buffer_is_not_a_dark():
    r = judge_dark(np.zeros((0, 0), dtype=np.uint16))
    assert not r.is_dark
    assert r.verdict == "empty"


def test_unsigned_pixels_near_zero_do_not_wrap_into_a_fake_saturation():
    """uint16 arithmetic: `img - median` on a frame sitting at 1 ADU wraps to
    65535 and would fabricate a saturated frame out of the darkest possible
    one."""
    rng = np.random.default_rng(31)
    frame = rng.integers(0, 3, (256, 256)).astype(np.uint16)
    r = judge_dark(frame, full_well=65535)
    assert r.saturated_pixels == 0
    assert r.spread < 10


# ------------------------------------------------------------ plumbing

def test_to_dict_carries_the_verdict_and_the_numbers():
    d = judge_dark(np.full((64, 64), 65535, dtype=np.uint16)).to_dict()
    assert set(d) == {"is_dark", "verdict", "level", "level_frac",
                      "level_frac_limit", "spread", "peak", "saturated_pixels",
                      "saturated_frac", "saturation_adu", "sources",
                      "source_zones", "opaque_slot", "reason"}
    assert isinstance(d["is_dark"], bool) and d["is_dark"] is False
    assert isinstance(d["reason"], str) and d["reason"]


def test_the_verdict_reaches_the_saved_file_as_header_cards():
    """A verdict that lives only in a preview event and a log line leaves the
    white frames on disk with nothing on them: the calibration library rebuilds
    masters by walking every *.fits under the capture root, keyed on headers
    alone. DARKOK is what it can filter on."""
    cards = judge_dark(np.full((64, 64), 65535, dtype=np.uint16),
                       full_well=65535).fits_cards()
    by_key = {k: (v, c) for k, v, c in cards}
    assert by_key["DARKOK"][0] is False
    assert by_key["DARKCHK"][0] == "saturated"
    assert "not a dark" in by_key["DARKWHY"][0]
    for key, (_, comment) in by_key.items():
        assert len(key) <= 8, f"{key} is not a legal FITS keyword"
        assert len(comment) <= 47                 # fits beside a short value
    assert judge_dark(bias_frame(), full_well=65535,
                      exposure_s=300).fits_cards()[0][1] is True


def test_thresholds_are_tunable_for_a_sensor_with_pathological_glow():
    """A frame with 3% of pixels clipped reads "not a dark" by default; a rig
    whose amp glow really does saturate that much can raise the bar."""
    frame = bias_frame()
    frame[:180, :180] = 65535                 # 3.1% of the frame
    assert not judge_dark(frame, full_well=65535).is_dark
    assert judge_dark(frame, full_well=65535, saturated_frac_max=0.05).is_dark


def test_the_judgement_never_mutates_the_caller_frame():
    """It runs on the same array the preview and the FITS write share."""
    frame = bias_frame()
    before = frame.copy()
    judge_dark(frame, full_well=65535, exposure_s=300)
    assert np.array_equal(frame, before)


# ------------------------------------------------------------------- the WIRING
#
# Everything above judges arrays. The judgement reached nothing: until 2026-08-04
# ``judge_dark`` had NO caller anywhere in astrodeck/ — a fully built, carefully
# tested detector, wired to nothing, with a docstring describing the incident it
# was going to prevent. These pin the two ends of the path it needs:
# capture STAMPS the verdict, and the library READS it.

@pytest.mark.asyncio
async def test_capture_stamps_the_verdict_into_a_white_dark(tmp_path, monkeypatch):
    """A daylight "dark" comes back pinned at the ceiling. The saved FITS must
    carry DARKOK=False, because the calibration library reads headers and
    nothing else."""
    from astropy.io import fits
    import astrodeck.hub as hub_module
    from astrodeck.hub import Hub

    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        real_expose = cam.expose

        async def white(*a, **kw):
            frame = await real_expose(*a, **kw)
            frame.data = np.full(frame.data.shape, 65535, dtype=np.uint16)
            frame.full_well = 65535
            return frame
        monkeypatch.setattr(cam, "expose", white)

        info = await h.capture(1.0, 100, 30, 1, save=True, frame_type="Dark")
        saved = Path(info["saved_path"] if "saved_path" in info
                     else h.last_frame.saved_path)
        hdr = fits.getheader(saved)
        assert hdr["DARKOK"] is False, "a white frame was filed as a good dark"
        assert hdr["DARKCHK"]
        assert hdr["DARKWHY"]
    finally:
        await h.disconnect_all()


@pytest.mark.asyncio
async def test_capture_passes_a_real_dark(tmp_path, monkeypatch):
    """The counterpart. A rejection that fires on ordinary darks is worse than
    no check — it would train the operator to ignore it."""
    from astropy.io import fits
    import astrodeck.hub as hub_module
    from astrodeck.hub import Hub

    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        info = await h.capture(1.0, 100, 30, 1, save=True, frame_type="Dark")
        saved = Path(info.get("saved_path") or h.last_frame.saved_path)
        assert fits.getheader(saved)["DARKOK"] is True
    finally:
        await h.disconnect_all()


@pytest.mark.asyncio
async def test_a_light_frame_gets_no_dark_cards(tmp_path, monkeypatch):
    """The check is about darks. A light is full of stars by design and must not
    be judged against a dark's expectations."""
    from astropy.io import fits
    import astrodeck.hub as hub_module
    from astrodeck.hub import Hub

    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        info = await h.capture(1.0, 100, 30, 1, save=True, frame_type="Light")
        hdr = fits.getheader(Path(info.get("saved_path") or h.last_frame.saved_path))
        assert "DARKOK" not in hdr
    finally:
        await h.disconnect_all()


def test_every_verdict_card_is_ascii():
    """A FITS header value may hold only printable ASCII. The reasons are
    written for a human and use typographic punctuation, so the fold has to
    cover every verdict — not just the one that happened to be tested."""
    frames = {
        "saturated": np.full((64, 64), 65535, dtype=np.uint16),
        "constant": np.full((64, 64), 500, dtype=np.uint16),
        "elevated": (np.random.default_rng(3).normal(20000, 50, (64, 64))
                     .astype(np.uint16)),
        "dark": (np.random.default_rng(4).normal(500, 20, (64, 64))
                 .astype(np.uint16)),
    }
    for label, arr in frames.items():
        cards = judge_dark(arr, full_well=65535, exposure_s=1.0).fits_cards()
        for keyword, value, _comment in cards:
            if isinstance(value, str):
                assert value.isascii() and value.isprintable(), \
                    f"{label}/{keyword} is not header-safe: {value!r}"


# ------------------------------------------- the star test does not scale up

class TestTheStarTestKnowsWhereItAppliesnt:
    """Every source constant in this module was measured on 1024x1024 frames.
    `SOURCE_MIN = 12` is a count PER MEGAPIXEL, and applying it unscaled to a
    26 MP sensor compares a big frame's source count against a small frame's
    bar.

    MEASURED on the rig, 2026-08-14. Six consecutive 180 s frames whose
    background was identical to four decimal places (median 240.0, spread
    4.4478) produced source counts 6, 8, 9, 19, 10, 13 - and the two that
    happened to land above 12 were condemned as light leaks. They are black:
    their brightest pixels share 4% with any real sub, and 10% with each other,
    which is noise agreeing with noise.

    The DARKOK=False those verdicts wrote makes `CalibrationLibrary` exclude
    the frames, so the check was quietly throwing away good darks.
    """

    def test_the_floor_scales_with_the_frame(self):
        from astrodeck.imaging.darks import SOURCE_MIN, source_floor
        assert source_floor(1024 * 1024) == SOURCE_MIN
        assert source_floor(4 * 1024 * 1024) == 4 * SOURCE_MIN

    def test_it_never_scales_BELOW_the_measured_bar(self):
        """A small frame keeps the measured constant. Scaling down would invent
        a bar looser than anything that was ever tested."""
        from astrodeck.imaging.darks import SOURCE_MIN, source_floor
        assert source_floor(256 * 256) == SOURCE_MIN

    def test_past_the_detectors_own_cap_it_ABSTAINS(self):
        """`detect_stars` stops at DEFAULT_MAX_STARS, so above about 17 MP the
        scaled bar is higher than the most sources it can ever return. The test
        could not fire however bright the leak, and a test that cannot fire has
        to say so rather than sit there looking like a pass."""
        from astrodeck.imaging.darks import source_floor
        from astrodeck.imaging.stars import DEFAULT_MAX_STARS
        assert source_floor(26 * 1024 * 1024) is None
        assert source_floor(int(DEFAULT_MAX_STARS / 12 * 1024 * 1024)) is not None

    def test_a_frame_it_cannot_judge_is_called_dark_and_SAYS_WHY(self, monkeypatch):
        """The abstention has to reach the operator sentence. A frame the star
        test skipped reads exactly like one that passed it, and the person who
        later finds a leaked dark in their library deserves to know which."""
        import astrodeck.imaging.stars as stars_mod
        monkeypatch.setattr(stars_mod, "DEFAULT_MAX_STARS", 5)
        r = judge_dark(real_frame("star_field"))
        assert r.is_dark, "it must not condemn a frame it admits it cannot judge"
        assert "does not apply" in r.reason, r.reason
        assert "MP" in r.reason

    def test_the_star_verdict_STILL_FIRES_where_it_was_measured(self):
        """The guard must not have deleted the feature. The real star field
        is the fixture the whole star branch was built on, and at its own
        size it is still condemned."""
        r = judge_dark(real_frame("star_field"))
        assert not r.is_dark and r.verdict == "stars", r.reason
