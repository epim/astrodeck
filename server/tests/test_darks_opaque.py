"""A dark shot through the "opaque" slot must be checked, not believed.

The rig case (2026-08-01): the slot the operator ticked as blackout was empty,
so 1s/4s/10s "darks" came back median 65535 max 65535 and were filed as a dark
library. The trap in fixing that is ``max``: the committed REAL overcast-sky
frame has max 65535 too, from a single hot pixel. Every rejection here keys off
the frame's bulk, never off its brightest pixel.

Fixtures: the two ``star_noise`` .npz files are REAL frames off this rig (a
fully-clouded sub and a Milky Way field) and are used for the hot-pixel and
faint-light cases, where real pixel statistics are the whole point. Everything
else is synthetic — daylight-through-an-empty-slot, amp glow and a railed 12-bit
readout are conditions we deliberately never want to reproduce on hardware.
"""
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging.darks import DarkResult, judge_dark

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


# ------------------------------------------- what must NOT be condemned

def test_one_saturated_hot_pixel_does_not_condemn_a_real_frame():
    """REAL fixture. blank_overcast has max 65535 — the identical max as the
    daylight leak — from exactly one hot pixel out of 1,048,576. A max-based
    check condemns it; a median-based one does not."""
    frame = real_frame("blank_overcast")
    assert int(frame.max()) == 65535, "fixture no longer carries the hot pixel"
    r = judge_dark(frame, full_well=65535)
    assert r.is_dark
    assert r.saturated_pixels == 1
    assert r.peak == 65535.0 and r.level < 300


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


def test_amp_glow_in_one_corner_does_not_condemn_the_frame():
    """A 72x72 saturated corner is 0.49% of a 1024x1024 frame — under the bar,
    and it cannot move the median at all."""
    frame = bias_frame()
    frame[:72, :72] = 65535
    r = judge_dark(frame, full_well=65535)
    assert r.is_dark
    assert r.saturated_frac < 0.005
    assert r.level < 300                      # the corner never touched the bulk


def test_a_warm_sensor_dark_is_not_flagged():
    """Elevated by dark current, not by light: 3% of full well."""
    r = judge_dark(bias_frame(level=2000.0, sigma=180.0), full_well=65535)
    assert r.is_dark
    assert r.verdict == "dark"


def test_a_long_exposure_with_real_dark_current_is_not_flagged():
    """A 600 s dark at high gain: 14% of full well, still far under the bar."""
    r = judge_dark(bias_frame(level=9000.0, sigma=700.0), full_well=65535)
    assert r.is_dark
    assert 0.1 < r.level_frac < 0.2


def test_a_quantised_low_gain_bias_is_not_mistaken_for_a_railed_readout():
    """Read noise under 1 ADU puts half the pixels at the maximum value — the
    same pile-up a rail produces. It also puts the other half at the minimum,
    which a rail never does."""
    rng = np.random.default_rng(5)
    frame = (100 + rng.integers(0, 2, (512, 512))).astype(np.uint16)
    r = judge_dark(frame, full_well=65535)
    assert r.is_dark
    assert r.verdict == "dark"


def test_the_accepted_sentence_says_plausible_not_verified():
    """One frame cannot prove no light reached the sensor, and the copy must
    not imply it did."""
    r = judge_dark(bias_frame(), full_well=65535)
    assert r.reason.startswith("plausible dark")
    assert "264" in r.reason


# ------------------------------------------------- the boundary, stated

def test_a_faint_light_frame_is_not_detectable_by_level_alone():
    """REAL fixture, and an honest limit rather than a bug: a 4 s Milky Way sub
    sits at 310 ADU, indistinguishable from a bias pedestal. This module
    reports "plausible", and the thing that would settle it is a second
    exposure — dark current scales with time from the pedestal, a leak from
    zero. Asserted so the limit stays visible if the thresholds ever move."""
    r = judge_dark(real_frame("star_field"), full_well=65535)
    assert r.is_dark
    assert r.level < 400 and r.peak > 7000    # real stars, and still "plausible"


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
    assert set(d) == {"is_dark", "verdict", "level", "level_frac", "spread",
                      "peak", "saturated_pixels", "saturated_frac",
                      "saturation_adu", "reason"}
    assert isinstance(d["is_dark"], bool) and d["is_dark"] is False
    assert isinstance(d["reason"], str) and d["reason"]


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
    judge_dark(frame, full_well=65535)
    assert np.array_equal(frame, before)
