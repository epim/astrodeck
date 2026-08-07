"""#156 — an overexposed sweep must not be told to expose LONGER.

2026-08-06, on the sky: autofocus at 3s/gain 200 failed with "lack of stars"
while the operator could see a field full of them — their own 2s/gain 120 frame
of the same sky held 200 measurable stars. The AF frames were overexposed until
the stars merged into railed blobs, the detector honestly reported almost
nothing, and every starvation path in the advice reads "too few stars" as
"needs more light". The one change the copy recommended was the one change
guaranteed to make the next run worse.

The fix is one cheap fact — what fraction of the frame sits at the container's
ceiling — consulted wherever a frame goes unmeasurable, flipping the advice
from "more light" to "less". Thresholds are metered, not guessed: across every
frame this rig captured over two weeks (rich fields, 10 s exposures, gain 300)
the worst healthy frame clipped 0.0015% of its sampled pixels; the gate sits at
0.2%, two orders of magnitude above.
"""
import numpy as np
import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus import run_autofocus
from astrodeck.imaging.stars import OVEREXPOSED_FRAC, saturation_fraction
import astrodeck.focus.autofocus as A
import astrodeck.focus.native as N

native_only = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


async def _connected_sim():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return parts["_rig"], cam, foc


def _focus_events(q):
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "focus":
            out.append(ev.data)
    return out


def _rail_camera(cam, *, after: int = 0):
    """Make ``cam`` return fully-railed frames, optionally only after the first
    ``after`` exposures (0 = every frame). Mutates in place, like the sky did."""
    orig = cam.expose
    calls = {"n": 0}

    async def railed(*a, **kw):
        frame = await orig(*a, **kw)
        calls["n"] += 1
        if calls["n"] > after:
            frame.data[:] = np.iinfo(frame.data.dtype).max
        return frame

    cam.expose = railed


# ------------------------------------------------------------------ the metric

def test_a_railed_frame_reads_fully_saturated():
    frame = np.full((256, 256), 65535, dtype=np.uint16)
    assert saturation_fraction(frame) == 1.0


def test_a_left_shifted_sensor_rail_still_registers():
    """A 12-bit sensor shifted into a 16-bit container rails at 65520, not
    65535 — the guide camera does exactly this. 0.98 of the ceiling, not
    equality, is what catches it."""
    frame = np.full((256, 256), 4095 << 4, dtype=np.uint16)
    assert saturation_fraction(frame) == 1.0


def test_a_healthy_frame_with_bright_star_cores_stays_below_the_gate():
    """The shape of every real frame this rig has captured: a low pedestal and
    a handful of pixels at the ceiling (star cores, hot pixels). The worst real
    frame metered 0.0015%; this synthetic one is 10x worse and must STILL sit
    under the gate."""
    rng = np.random.default_rng(42)
    frame = rng.integers(200, 400, size=(1024, 1024)).astype(np.uint16)
    n_hot = int(frame.size * 0.00015)          # 10x the worst healthy frame
    ys = rng.integers(0, 1024, n_hot)
    xs = rng.integers(0, 1024, n_hot)
    frame[ys, xs] = 65535
    assert saturation_fraction(frame) < OVEREXPOSED_FRAC


def test_dark_frames_do_not_read_saturated():
    """All-zero and near-zero frames are underexposure, not clipping — a black
    frame whose every pixel equals its own max must not count as railed."""
    assert saturation_fraction(np.zeros((64, 64), dtype=np.uint16)) == 0.0
    assert saturation_fraction(np.zeros((64, 64), dtype=np.float64)) == 0.0


def test_a_normalized_float_frame_rails_at_one():
    frame = np.ones((64, 64), dtype=np.float64)
    assert saturation_fraction(frame) == 1.0


# ------------------------------------------------------- native path, the probe

@native_only
async def test_an_overexposed_probe_blames_the_light_not_the_sky(monkeypatch):
    """The 2026-08-06 failure, exactly: a railed frame, a detector that honestly
    finds one merged blob, and a refusal that used to read "lack of stars — try
    a longer exposure". The pixels say why the stars are missing, and the
    advice must run the other way."""
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    _rail_camera(cam)
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 1,
                                                   "hfr_median": None,
                                                   "hfr_mad": 0.0}))

    q = bus.subscribe()
    try:
        res = await N.run_native_autofocus(cam, foc, exposure_s=3.0, gain=200,
                                           step=350, steps_each_side=4,
                                           binning=2)
    finally:
        bus.unsubscribe(q)

    assert res.success is False
    # the diagnosis names the clipping, with the measured fraction in it
    assert "overexposed" in res.message, res.message
    assert "full scale" in res.message, res.message
    # the fix points at LESS light, and never contradicts itself
    assert res.advice, "the refusal carried no advice"
    assert "shorter exposure than 3s" in res.advice, res.advice
    assert "less gain than 200" in res.advice, res.advice
    assert "longer" not in res.advice, res.advice
    assert "richer field" not in res.advice, res.advice
    # refusal discipline unchanged: focuser untouched, panel told
    assert await foc.get_position() == start
    events = _focus_events(q)
    assert events[-1]["state"] == "failed"
    assert events[-1].get("advice") == res.advice


@native_only
async def test_a_starless_probe_that_is_not_clipped_keeps_the_old_advice(monkeypatch):
    """The gate must not rewrite the genuinely-sparse refusal: a dark, healthy
    frame with two stars still earns "longer exposure"."""
    _rig, cam, foc = await _connected_sim()
    monkeypatch.setattr(N._native, "detect_and_measure",
                        lambda data, params: ([], {"star_count": 2,
                                                   "hfr_median": 3.0,
                                                   "hfr_mad": 0.1}))
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       binning=2)
    assert res.success is False
    assert "overexposed" not in res.message, res.message
    assert res.advice and "longer exposure" in res.advice, res.advice


# ------------------------------------------------------ native path, mid-sweep

@native_only
async def test_clipped_sweep_frames_flip_the_advice(monkeypatch):
    """A probe that measures fine, then sweep frames that rail (twilight
    brightening, a gain change, the field rotating onto a bright star): the
    dropped points must be filed as CLIPPED, and the levers must point at less
    light — the sparse-field "longer exposure" line would be the same wrong
    turn the probe gate exists to stop."""
    _rig, cam, foc = await _connected_sim()
    _rail_camera(cam, after=1)                  # the probe stays healthy
    calls = {"n": 0}

    def detector(data, params):
        calls["n"] += 1
        if calls["n"] == 1:                     # the pre-sweep probe
            return [], {"star_count": 200, "hfr_median": 3.0, "hfr_mad": 0.2}
        return [], {"star_count": 0, "hfr_median": None, "hfr_mad": 0.0}

    monkeypatch.setattr(N._native, "detect_and_measure", detector)
    monkeypatch.setattr(N, "native_sweep_metric", lambda data: (None, 0))

    res = await N.run_native_autofocus(cam, foc, exposure_s=3.0, gain=200,
                                       step=350, steps_each_side=4, binning=2)

    assert res.success is False
    assert res.advice, "the clipped drops never reached the advice"
    assert "overexposed" in res.advice, res.advice
    assert "full scale" in res.advice, res.advice
    assert "shorter exposure than 3s" in res.advice, res.advice
    assert "less gain than 200" in res.advice, res.advice
    assert "longer exposure" not in res.advice, res.advice


# ------------------------------------------------------------ legacy numpy path

async def test_legacy_clipped_drops_flip_the_advice_and_silence_the_metric(monkeypatch):
    """The no-provider numpy path has the same duty. Its extra trap: the size
    metric's own note for an unmeasurable frame says "no source rose above the
    noise… try a longer exposure", which is exactly what a railed frame looks
    like from inside a star detector. Beside a clipping finding that note must
    be withheld, not printed as a rival instruction."""
    _rig, cam, foc = await _connected_sim()
    _rail_camera(cam)
    monkeypatch.setattr(
        A, "sweep_metric",
        lambda data, min_stars=3: (None, 0,
                                   "No source rose above the noise anywhere in "
                                   "the frame. Try 8s instead of 2s."))

    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)

    assert res.success is False
    assert res.advice, "the failure carried no advice"
    assert "overexposed" in res.advice, res.advice
    assert "shorter exposure than 2s" in res.advice, res.advice
    assert "less gain than 200" in res.advice, res.advice
    assert "longer exposure" not in res.advice, res.advice
    assert "8s instead" not in res.advice, res.advice


async def test_legacy_unclipped_drops_keep_the_metric_note_and_old_levers(monkeypatch):
    """And the flip only happens on clipped evidence — a genuinely starless
    legacy sweep keeps both the metric's note and the longer-exposure levers."""
    _rig, cam, foc = await _connected_sim()
    monkeypatch.setattr(
        A, "sweep_metric",
        lambda data, min_stars=3: (None, 0, "No source rose above the noise."))

    res = await run_autofocus(cam, foc, exposure_s=2.0, gain=200, step=350,
                              steps_each_side=4, binning=2)

    assert res.success is False
    assert res.advice
    assert "No source rose above the noise." in res.advice, res.advice
    assert "longer exposure than 2s" in res.advice, res.advice
    assert "overexposed" not in res.advice, res.advice
