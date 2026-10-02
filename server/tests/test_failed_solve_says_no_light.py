# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A failed plate solve says whether light reached the sensor (#251).

On 2026-09-24/25 auto-resume logged "plate solve failed: Not enough stars."
every ten minutes for two and a half hours under a clear sky, because the
optic was capped: the last solve frame (12 s, gain 200, bin 2, 18.5 C) read
median 251, p1 231, p99 273, std 32.6, the statistics of a dark. A covered
optic says "not enough stars" in the same words as a cloud, and nothing read
the frame that already held the difference.

``astrodeck.solve.light`` reads it: a failed solve's frame is judged by its
median against the median this camera reads with no light on it (the dark
library's master, or a bias plus the least dark current the doubling law
allows). These tests hold the verdicts on synthetic frames with an explicit
reference, the reference rules on a table of masters, every solve path that
raises on a failure, and a static scan for a solve path that raises without
asking.

Each case names the mutation that turns it red and the failure it produced,
verbatim, from a run of that mutant against a byte copy of the file.
"""
from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.calibration.matcher import MasterRecord
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.solve import light
from astrodeck.solve.base import SolveResult

FIXTURE = (Path(__file__).parent / "fixtures" / "star_noise"
           / "blank_overcast.npz")

#: Big enough that a median is a median, small enough to be quick.
SHAPE = (480, 640)

#: The dark master a #251-like frame would be measured against: one ADU below
#: the frame, as a master and tonight's frame differ when nothing is wrong.
REF = light.Reference(level=250.0, source="a test dark master")


def _no_light_frame(level: float = 251.0, sigma: float = 9.0,
                    hot_frac: float = 0.006, seed: int = 251) -> np.ndarray:
    """A frame with no light on it, shaped by the #251 evidence: a Gaussian
    core at the dark level (median 251; p1 and p99 of 231 and 273 put sigma at
    9.0) and the warm pixels of an 18.5 C sensor that took the std to 32.6."""
    rng = np.random.default_rng(seed)
    a = rng.normal(level, sigma, SHAPE)
    n_hot = int(hot_frac * a.size)
    idx = rng.choice(a.size, n_hot, replace=False)
    a.flat[idx] = level + rng.uniform(150.0, 600.0, n_hot)
    return np.clip(np.rint(a), 0, 65535).astype(np.uint16)


def _flat_sky_frame(pedestal: float = 251.0, sky: float = 40.0,
                    seed: int = 252) -> np.ndarray:
    """The same sensor under an overcast sky: the dark frame plus a starless
    background ``sky`` ADU deep at the centre, vignetted to 80 % in the
    corners, with its own shot noise. No stars, so a solver says what it said
    on #251."""
    rng = np.random.default_rng(seed)
    base = _no_light_frame(level=pedestal, seed=seed).astype(np.float64)
    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    r2 = (((yy - SHAPE[0] / 2) / SHAPE[0]) ** 2
          + ((xx - SHAPE[1] / 2) / SHAPE[1]) ** 2) / 0.5
    bg = sky * (1.0 - 0.2 * r2)
    a = base + bg + rng.normal(0.0, np.sqrt(bg))
    return np.clip(np.rint(a), 0, 65535).astype(np.uint16)


def _frame(data: np.ndarray, *, seconds: float = 12.0, gain: int = 200,
           offset: int = 30, binning: int = 2, temp: float | None = 18.5,
           linear: bool = True) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=seconds, gain=gain, offset=offset,
                       binning=binning, bayer_pattern=None, temperature_c=temp,
                       timestamp=0.0, data_is_linear=linear)


# ============================================================ the verdicts

def test_the_synthetic_dark_is_the_251_frame():
    """Premise for the cases below: the synthetic no-light frame carries the
    #251 frame's numbers, so a verdict on it is a verdict on that night."""
    a = _no_light_frame().astype(np.float64)
    assert np.median(a) == pytest.approx(251.0, abs=1.0)
    assert np.percentile(a, 1) == pytest.approx(231.0, abs=3.0)
    assert np.percentile(a, 99) == pytest.approx(273.0, abs=3.0)
    assert a.std() == pytest.approx(32.6, rel=0.25)


def test_a_bias_level_frame_is_no_light():
    """The #251 frame against its dark master: no light.

    RED under mutant "classify by star count only" (``classify`` counts
    stars with ``imaging.stars.detect_stars`` and calls a frame with too few
    of them "not enough stars", never reading its level), observed
    verbatim:

        E   AssertionError: LightVerdict(kind='cloud', median=251.0, pixel_sigma=8.8956, band=None, reference=Reference(level=250.0, sigma=1.0, source='a test dark master', detail=''), why='6 stars found')
        E   assert 'cloud' == 'no_light'
    """
    v = light.classify(_no_light_frame(), REF)
    assert v.kind == light.NO_LIGHT, v


def test_a_flat_starless_sky_above_the_reference_is_cloud():
    """The same sensor with 40 ADU of starless overcast on it: light reached
    the sensor, so the verdict is cloud, though the frame has no more stars
    than the capped one.

    RED under mutant "no upper edge to the band" (the ``excess > band``
    branch removed, so anything at or above the reference is no light),
    observed verbatim:

        E   AssertionError: LightVerdict(kind='no_light', median=288.0, pixel_sigma=14.825999999999999, band=3.0016854502447314, reference=Reference(level=250.0, sigma=1.0, source='a test dark master', detail=''), why='')
        E   assert 'no_light' == 'cloud'
    """
    v = light.classify(_flat_sky_frame(), REF)
    assert v.kind == light.CLOUD, v
    assert v.median - REF.level > 30.0, v


def test_no_reference_is_never_no_light():
    """No reference, no verdict: the capped frame and the sky frame both come
    back unknown, and so does a frame sitting exactly where a dark would.

    RED under mutant "no reference counts as no light" (``classify`` returns
    NO_LIGHT when handed no reference), observed verbatim:

        E   AssertionError: LightVerdict(kind='no_light', median=251.0, pixel_sigma=8.8956, band=None, reference=None, why='there is no no-light reference for this camera')
        E   assert 'no_light' == 'unknown'
    """
    for data in (_no_light_frame(), _flat_sky_frame(),
                 _no_light_frame(level=REF.level)):
        v = light.classify(data, None)
        assert v.kind == light.UNKNOWN, v
        assert v.why == light.NO_REFERENCE_WHY


def test_the_real_overcast_fixture_never_reads_as_no_light():
    """The committed overcast frame (4 s, gain 220, bin 1, 100 % cloud, no
    sources; median 264, per-pixel robust sigma 22.2) against every integer
    reference level from 0 to 260.

    WHY IT STOPS AT 260 AND NOT AT 263.9, which is what "any reference below
    its level" says literally. A reference within the band of the frame
    describes a frame with no measurable light in it, and a level test with
    any positive tolerance calls that frame no light: that is its job. The
    band is 3.0 ADU (``K_SIGMA`` x ``OFFSET_STABILITY_ADU``), so 261, 262 and
    263 are inside it. The sweep's top is fixed here, not computed from the
    module's constants, so widening the band turns this red instead of
    shrinking the sweep. The level that matters is inside it: 251, what the
    #251 capped frame read on the same 26 MP sensor family, 13 ADU below this
    overcast sky.

    RED under mutant "band in per-pixel sigma" (the band is ``K_SIGMA`` times
    the frame's own robust per-pixel sigma, the obvious unit), observed
    verbatim:

        E   AssertionError: the real overcast fixture read as no light against references at [198, 199, 200, 201, 202]...[256, 257, 258, 259, 260] ADU
        E   assert not [198, 199, 200, 201, 202, 203, ...]
    """
    data = np.load(FIXTURE)["data"]
    assert float(np.median(data)) == 264.0, "premise: the fixture's level"
    lit = []
    for level in range(0, 261):
        v = light.classify(data, light.Reference(level=float(level)))
        if v.kind == light.NO_LIGHT:
            lit.append(level)
    assert not lit, (f"the real overcast fixture read as no light against "
                     f"references at {lit[:5]}...{lit[-5:]} ADU")


def test_a_frame_darker_than_its_reference_gets_no_verdict():
    """A frame 20 ADU below its reference cannot be a no-light frame: nothing
    reads darker than the dark. The reference is wrong for it, so there is no
    verdict.

    RED under mutant "darker counts as no light" (the ``excess < -band``
    branch removed), observed verbatim:

        E   AssertionError: LightVerdict(kind='no_light', median=230.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=250.0, sigma=1.0, source='a test dark master', detail=''), why='')
        E   assert 'no_light' == 'unknown'
    """
    v = light.classify(_no_light_frame(level=230.0), REF)
    assert v.kind == light.UNKNOWN, v
    assert "darker" in v.why


def test_a_constant_buffer_gets_no_verdict():
    """Every pixel at exactly the reference: a buffer, not an exposure.

    RED under mutant "no constant-buffer check", observed verbatim:

        E   AssertionError: LightVerdict(kind='no_light', median=250.0, pixel_sigma=0.0, band=3.0, reference=Reference(level=250.0, sigma=1.0, source='a test dark master', detail=''), why='')
        E   assert 'no_light' == 'unknown'
    """
    v = light.classify(np.full(SHAPE, 250, np.uint16), REF)
    assert v.kind == light.UNKNOWN, v


async def test_every_verdict_explains_itself_without_raising(tmp_path,
                                                             bus_lines):
    """Through ``failed_solve_error``, the one call every solve path makes:
    each verdict comes back as an exception to raise, and its evidence line
    is logged, including the verdicts judged against a reference that never
    drew a band (a constant buffer). A level check that crashes would turn a
    failed solve into a crash of the path that asked.

    RED under mutant "the band is always formatted" (``evidence`` formats
    ``band`` whether or not there is one), observed verbatim:

        E   TypeError: unsupported format string passed to NoneType.__format__
    """
    hub = type("H", (), {"master_library": _Library([_write_master(
        tmp_path, "DARK", 250.0)])})()
    frames = {light.NO_LIGHT: _no_light_frame(),
              light.CLOUD: _flat_sky_frame(),
              "buffer": np.full(SHAPE, 250, np.uint16)}
    for name, data in frames.items():
        e = await light.failed_solve_error(
            _frame(data), SolveResult(False, message="Not enough stars."),
            prefix="plate solve failed", hub=hub)
        assert isinstance(e, light.FailedSolveError), (name, e)
        want = light.UNKNOWN if name == "buffer" else name
        assert e.verdict.kind == want, (name, e.verdict)
    evidence = [m for lv, m, src in bus_lines
                if src == "solve" and "light check" in m]
    assert len(evidence) == 3, bus_lines
    assert not [m for lv, m, src in bus_lines if lv == "warning"], bus_lines


async def test_a_level_check_that_crashes_is_no_verdict(tmp_path, bus_lines,
                                                        monkeypatch):
    """The level check itself failing is no verdict: never a no-light one, and
    never a crash of the solve path that asked (``failed_solve_error``'s
    promise, which nothing else here reaches: every other case hands it a
    check that works). The crash is the frame's statistics running out of
    memory, as a float copy of a full frame can on an Orange Pi. The frame and
    the library are the ones that give a no-light verdict while the check
    works, so a fallback that said "no light" would show.

    RED under mutant "a crashed check says no light" (the fallback verdict is
    NO_LIGHT, not UNKNOWN), observed verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True
        E    +  where True = isinstance(NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)'), <class 'astrodeck.solve.light.NoLightError'>)

    RED under mutant "no crash guard" (the ``except`` narrowed to
    ``ZeroDivisionError``, so the crash escapes), observed verbatim:

        E   MemoryError: a float copy of the frame
    """
    hub = type("H", (), {"master_library": _Library([_write_master(
        tmp_path, "DARK", 250.0)])})()
    frame = _frame(_no_light_frame())
    failed = SolveResult(False, message="Not enough stars.")
    works = await light.failed_solve_error(frame, failed,
                                           prefix="plate solve failed", hub=hub)
    assert isinstance(works, light.NoLightError), "premise: the check works"

    def out_of_memory(data):
        raise MemoryError("a float copy of the frame")
    monkeypatch.setattr(light, "frame_stats", out_of_memory)
    e = await light.failed_solve_error(frame, failed,
                                       prefix="plate solve failed", hub=hub)
    assert not isinstance(e, light.NoLightError), e
    assert isinstance(e, light.FailedSolveError), e
    assert e.verdict.kind == light.UNKNOWN, e.verdict
    assert str(e) == ("plate solve failed: Not enough stars. (no level check "
                      "was possible: the level check itself failed)"), e
    warned = [m for lv, m, src in bus_lines if lv == "warning" and src == "solve"]
    assert warned == ["solve light check failed: a float copy of the frame"], \
        bus_lines


# ============================================================ the reference

def _rec(id_: str, kind: str, *, seconds: float = 12.0, gain: int = 200,
         offset: int = 30, temp: float | None = 18.5, binning: int = 2,
         path: str = "") -> MasterRecord:
    return MasterRecord(id=id_, frame_type=kind,
                        exposure_s=0.0 if kind == "BIAS" else seconds,
                        gain=gain, offset=offset, temp_c=temp, binning=binning,
                        filter="", frame_count=20, path=path or id_,
                        built_ts=1.0)


def _levels(table: dict[str, float]):
    return lambda rec: table.get(rec.id)


async def test_the_matched_dark_master_is_the_reference():
    """A dark master at the frame's settings wins over a bias that is also
    there. The dark is 0.4 s and 1 C off the frame, inside the matcher's
    window (5 %, 2 C), so the bias path, which would take that same dark as
    its dark-current anchor and scale it, lands somewhere else: with the dark
    at the frame's exact settings the two paths agree to the ADU, and a first
    version of this case, built that way, passed under the mutant below.

    RED under mutant "bias before dark" (``reference_for`` asks for the bias
    first), observed verbatim:

        E   AssertionError: Reference(level=250.68473722910142, sigma=1.0, source='the bias master plus the least dark current the doubling law al...'bias master bias; dark current 0.8065 ADU/s from dark master dark, scaled +1.0 C at one doubling per 7 C to 10.7 ADU')
        E   assert 250.68473722910142 == 250.0
    """
    masters = [_rec("bias", "BIAS"),
               _rec("dark", "DARK", seconds=12.4, temp=17.5)]
    ref, why = light.reference_for(_frame(_no_light_frame()), masters,
                                   level_of=_levels({"bias": 240.0,
                                                     "dark": 250.0}))
    assert why == ""
    assert ref.level == 250.0, ref
    assert "dark master dark" in ref.detail


@pytest.mark.parametrize("frame_temp, anchor_temp, anchor_s, anchor_level, "
                         "frame_s, want", [
    # WARMER: a 60 s dark at -10 C reading 6 ADU over the bias (0.1 ADU/s),
    # scaled to the 12 s, 18.5 C solve frame at the SLOW doubling:
    # 0.1 x 12 x 2^(28.5 / 7) = 20.17 ADU over the 240 ADU bias.
    (18.5, -10.0, 60.0, 246.0, 12.0, 240.0 + 0.1 * 12 * 2 ** (28.5 / 7)),
    # COLDER: a 60 s dark at +10 C reading 60 ADU over the bias (1 ADU/s),
    # scaled to a 60 s frame at -15 C at the FAST doubling:
    # 1 x 60 x 2^(-25 / 5) = 1.875 ADU.
    (-15.0, 10.0, 60.0, 300.0, 60.0, 240.0 + 1.0 * 60 * 2 ** (-25 / 5)),
])
async def test_a_bias_plus_the_least_dark_current(frame_temp, anchor_temp,
                                                   anchor_s, anchor_level,
                                                   frame_s, want):
    """No dark at the frame's settings: the bias, plus the dark current a
    dark at another temperature measured, scaled at whichever end of the
    doubling range gives LESS of it.

    RED under mutant "nominal doubling" (both directions scale at 6 C),
    observed verbatim:

        E   AssertionError: Reference(level=272.2904223457426, sigma=1.0, source='the bias master plus the least dark current the doubling law all...as master bias; dark current 0.1000 ADU/s from dark master anchor, scaled +28.5 C at one doubling per 6 C to 32.3 ADU')
        E   assert 272.2904223457426 == 260.1745274621418 ± 1.0e-06
        E   AssertionError: Reference(level=243.34087019302626, sigma=1.0, source='the bias master plus the least dark current the doubling law al...ias master bias; dark current 1.0000 ADU/s from dark master anchor, scaled -25.0 C at one doubling per 6 C to 3.3 ADU')
        E   assert 243.34087019302626 == 241.875 ± 1.0e-06

    RED under mutant "fast doubling when warmer", observed verbatim:

        E   AssertionError: Reference(level=302.3809840401589, sigma=1.0, source='the bias master plus the least dark current the doubling law all...as master bias; dark current 0.1000 ADU/s from dark master anchor, scaled +28.5 C at one doubling per 5 C to 62.4 ADU')
        E   assert 302.3809840401589 == 260.1745274621418 ± 1.0e-06

    RED under mutant "slow doubling when colder", observed verbatim:

        E   AssertionError: Reference(level=245.04712572237133, sigma=1.0, source='the bias master plus the least dark current the doubling law al...ias master bias; dark current 1.0000 ADU/s from dark master anchor, scaled -25.0 C at one doubling per 7 C to 5.0 ADU')
        E   assert 245.04712572237133 == 241.875 ± 1.0e-06
    """
    masters = [_rec("bias", "BIAS", temp=-10.0),
               _rec("anchor", "DARK", seconds=anchor_s, temp=anchor_temp)]
    frame = _frame(_no_light_frame(), seconds=frame_s, temp=frame_temp)
    ref, why = light.reference_for(frame, masters,
                                   level_of=_levels({"bias": 240.0,
                                                     "anchor": anchor_level}))
    assert why == ""
    assert ref.level == pytest.approx(want, abs=1e-6), ref


async def test_a_bias_alone_is_the_reference():
    """A bias and no dark at this readout: the dark current's lower bound is
    zero, so the bias itself is the reference.

    RED under mutant "bias alone is no reference" (``reference_for`` returns
    None when there is no dark to scale from), observed verbatim:

        E   AssertionError: assert 'no dark to scale from' == ''
    """
    ref, why = light.reference_for(_frame(_no_light_frame()),
                                   [_rec("bias", "BIAS", temp=-10.0)],
                                   level_of=_levels({"bias": 240.0}))
    assert why == ""
    assert ref.level == 240.0, ref
    assert "no dark current is assumed" in ref.detail


# SEVERAL MASTERS OF ONE KIND AT ONE READOUT (#274, H3 review). Every case
# above ever hands ``reference_for`` a single bias and a single dark anchor,
# so the picking rules (nearest in temperature) never had a choice to get
# wrong. These two put two of each in front of it.

async def test_two_biases_the_nearer_in_temperature_sets_the_floor():
    """Two bias masters at this readout, far apart in temperature: the floor
    is the NEARER one's level, never the farther's, however many frames
    stand behind it (``_nearest_bias`` takes ANY temperature, so this is
    the only axis that can decide between them here).

    RED under mutant "farthest bias" (``_nearest_bias``'s ``min`` made
    ``max``), observed verbatim:

        E   AssertionError: Reference(level=300.0, sigma=1.0, source='the bias master plus the least dark current the doubling law allows', detail='bias master far; no dark to scale from, so no dark current is assumed')
        E   assert 300.0 == 242.0
    """
    masters = [_rec("near", "BIAS", temp=18.0),
               _rec("far", "BIAS", temp=-10.0)]
    ref, why = light.reference_for(_frame(_no_light_frame(), temp=18.5),
                                   masters,
                                   level_of=_levels({"near": 242.0,
                                                     "far": 300.0}))
    assert why == ""
    assert ref.level == 242.0, ref
    assert "bias master near" in ref.detail, ref.detail


async def test_two_dark_anchors_the_nearer_in_temperature_sets_the_scaling():
    """Two dark masters that each qualify as a scaling anchor (same readout,
    a real exposure, a known temperature), far apart in temperature: the
    dark-current rate comes from the NEARER one, so the scaling extrapolates
    least, never the farther, which (the module docstring says) widens the
    no-light band and would swallow a fainter sky. Both anchors are shot at
    60 s against the frame's 12 s, outside the matcher's 5 % window
    regardless of temperature, so this is purely ``_dark_anchor``'s choice,
    never option 1's.

    RED under mutant "farthest anchor" (``_dark_anchor``'s ``min`` made
    ``max``), observed verbatim:

        E   AssertionError: Reference(level=441.7452746214181, sigma=1.0, source='the bias master plus the least dark current the doubling law all...bias master bias; dark current 1.0000 ADU/s from dark master far, scaled +28.5 C at one doubling per 7 C to 201.7 ADU')
        E   assert 441.7452746214181 == 241.3921552633922 ± 1.0e-06
    """
    masters = [_rec("bias", "BIAS", temp=5.0),
               _rec("near", "DARK", seconds=60.0, temp=17.0),
               _rec("far", "DARK", seconds=60.0, temp=-10.0)]
    frame = _frame(_no_light_frame(), seconds=12.0, temp=18.5)
    ref, why = light.reference_for(frame, masters,
                                   level_of=_levels({"bias": 240.0,
                                                     "near": 246.0,
                                                     "far": 300.0}))
    assert why == ""
    # The nearer anchor's rate: (246 - 240) / 60 = 0.1 ADU/s, scaled +1.5 C
    # (the frame is warmer than the anchor) at the slow doubling for the
    # floor and the fast one for the ceiling (reference_for's WARMER branch).
    want_floor = 240.0 + 0.1 * 12.0 * 2 ** (1.5 / 7)
    want_ceiling = 240.0 + 0.1 * 12.0 * 2 ** (1.5 / 5)
    assert ref.level == pytest.approx(want_floor, abs=1e-6), ref
    assert ref.ceiling == pytest.approx(want_ceiling, abs=1e-6), ref.ceiling
    assert "dark master near" in ref.detail, ref.detail


# A BIAS-BASED REFERENCE IS A FLOOR, NOT THE LEVEL (T9 verifier, H3). The
# bias plus the least dark current is the least the no-light level could be.
# Judged against that floor alone, a capped frame with more dark current than
# the floor allowed read as CLOUD, and was worded "light is reaching the
# sensor": the opposite of the truth, and a cloud verdict ends ResumeArm's
# no-light spell. These cases hold the ceiling: cloud only past the most the
# no-light level could be.

async def test_the_251_frame_over_a_bias_alone_is_no_verdict_not_cloud():
    """#251's capped frame (median 251 at 18.5 C) against a 240 ADU bias and
    nothing else, the least #262 asks an operator to shoot. With no dark to
    measure a rate from, nothing bounds the dark current from above, so 11
    ADU over the bias may be dark current alone: no verdict, and no clause
    saying light reached the sensor.

    RED under mutant "the ceiling ignored" (``classify``'s
    ``median - reference.top > band`` made ``True``), observed verbatim:

        E       AssertionError: LightVerdict(kind='cloud', median=251.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=240.0,...ent the doubling law allows', detail='bias master bias; no dark to scale from, so no dark current is assumed'), why='')
        E       assert 'cloud' == 'unknown'

    RED under mutant "a bias alone has a ceiling at the bias" (``dark_lo,
    dark_hi = 0.0, math.inf`` made ``0.0, 0.0`` in ``reference_for``),
    observed verbatim:

        E       assert 240.0 == inf
        E        +  where 240.0 = Reference(level=240.0, sigma=1.0, source='the bias master plus the least dark current the doubling law allows', detail='bias master bias; no dark to scale from, so no dark current is assumed').ceiling
    """
    ref, why = light.reference_for(_frame(_no_light_frame()),
                                   [_rec("bias", "BIAS", temp=-10.0)],
                                   level_of=_levels({"bias": 240.0}))
    assert why == "" and ref.level == 240.0, (ref, why)
    assert ref.ceiling == float("inf"), ref
    v = light.classify(_no_light_frame(), ref)
    assert v.median - ref.level > v.band, f"premise: above the floor: {v}"
    assert v.kind == light.UNKNOWN, v
    assert v.why == light.BELOW_THE_CEILING_WHY, v
    assert "with no ceiling" in v.evidence(), v.evidence()
    err = light.error_for(v, "Not enough stars.", "plate solve failed")
    assert not isinstance(err, light.NoLightError), err
    assert "light is reaching the sensor" not in str(err), err
    assert _no_digits(str(err)), f"the failure carries a number: {err}"


@pytest.mark.parametrize("frame_temp, anchor_temp, anchor_level, frame_s, "
                         "median, floor, ceiling", [
    # WARMER, the verifier's #251 probe shape: a 60 s dark at -10 C reading 2
    # ADU over the 240 ADU bias (2/60 ADU/s), scaled to the 12 s, 18.5 C
    # frame: floor at the slow end 240 + 0.4 x 2^(28.5/7) = 246.73, ceiling at
    # the fast end 240 + 0.4 x 2^(28.5/5) = 260.79. The capped frame's 251 is
    # 4.3 ADU over the floor (past the 3 ADU band) and 9.8 under the ceiling.
    (18.5, -10.0, 242.0, 12.0, 251.0,
     240.0 + 0.4 * 2 ** (28.5 / 7), 240.0 + 0.4 * 2 ** (28.5 / 5)),
    # COLDER: a 60 s dark at +10 C reading 60 ADU over the bias (1 ADU/s),
    # scaled to a 60 s frame at -15 C: floor at the fast end 240 + 60 x
    # 2^(-25/5) = 241.88, ceiling at the slow end 240 + 60 x 2^(-25/7) =
    # 245.05. A frame at 246 is 4.1 over the floor and 0.95 over the ceiling.
    (-15.0, 10.0, 300.0, 60.0, 246.0,
     240.0 + 60.0 * 2 ** (-25 / 5), 240.0 + 60.0 * 2 ** (-25 / 7)),
])
async def test_a_frame_between_the_floor_and_the_ceiling_is_no_verdict(
        frame_temp, anchor_temp, anchor_level, frame_s, median, floor,
        ceiling):
    """A dark at another temperature bounds the dark current both ways: the
    floor at the end of the doubling range that gives less of it, the
    ceiling at the end that gives more. A frame above the floor but short of
    the ceiling gets no verdict; the same reference under a real overcast
    (the control, 27 ADU and more past the ceiling) is still cloud.

    RED under mutant "the ceiling at the floor's end" (``other = doubling``
    in ``reference_for``, so the ceiling equals the floor), observed
    verbatim:

        E       assert 246.7248424873806 == 260.79366134671966 ± 1.0e-06
        E       assert 241.875 == 245.04712572237133 ± 1.0e-06

    RED under mutant "the ceiling ignored", observed verbatim:

        E       AssertionError: (LightVerdict(kind='cloud', median=251.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=246.7...0.0333 ADU/s from dark master anchor, scaled +28.5 C at one doubling per 7 C to 6.7 ADU'), why=''), 260.79366134671966)
        E       assert 'cloud' == 'unknown'
        E       AssertionError: (LightVerdict(kind='cloud', median=246.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=241.8...1.0000 ADU/s from dark master anchor, scaled -25.0 C at one doubling per 5 C to 1.9 ADU'), why=''), 245.04712572237133)
        E       assert 'cloud' == 'unknown'
    """
    masters = [_rec("bias", "BIAS", temp=-10.0),
               _rec("anchor", "DARK", seconds=60.0, temp=anchor_temp)]
    frame = _frame(_no_light_frame(level=median), seconds=frame_s,
                   temp=frame_temp)
    ref, why = light.reference_for(frame, masters,
                                   level_of=_levels({"bias": 240.0,
                                                     "anchor": anchor_level}))
    assert why == ""
    assert ref.level == pytest.approx(floor, abs=1e-6), ref
    assert ref.ceiling == pytest.approx(ceiling, abs=1e-6), ref.ceiling
    v = light.classify(frame.data, ref)
    assert v.median - ref.level > v.band, f"premise: above the floor: {v}"
    assert v.kind == light.UNKNOWN, (v, ref.ceiling)
    assert f"ceiling {ceiling:.1f} ADU" in v.evidence(), v.evidence()
    # The control: a sky well past the ceiling is light, whatever the floor.
    sky = light.classify(_flat_sky_frame(pedestal=median), ref)
    assert sky.median - ref.ceiling > 25.0, f"premise: past the ceiling: {sky}"
    assert sky.kind == light.CLOUD, sky


async def test_no_master_is_no_reference_and_says_what_is_missing():
    """Nothing at this readout: no reference, and the reason names the
    readout, which is what the operator has to shoot. A master at another
    gain is not this camera's reference.

    RED under mutant "a missing reference reads as level zero"
    (``reference_for`` returns ``Reference(level=0.0)`` when nothing
    matches), observed verbatim:

        E   AssertionError: assert Reference(level=0.0, sigma=1.0, source='an explicit reference', detail='') is None
    """
    ref, why = light.reference_for(_frame(_no_light_frame()),
                                   [_rec("other", "BIAS", gain=100)],
                                   level_of=_levels({"other": 240.0}))
    assert ref is None
    assert "no bias master at gain 200, offset 30, bin 2" in why, why


class _Library:
    def __init__(self, masters):
        self._masters = masters

    def list_masters(self):
        return list(self._masters)


def _write_master(tmp_path: Path, kind: str, level: float,
                  **kw) -> MasterRecord:
    """A master on disk, as ``CalibrationLibrary.build`` writes one: float32
    pixels around ``level``."""
    path = tmp_path / f"{kind.lower()}_{level:g}.fits"
    rng = np.random.default_rng(7)
    fits.PrimaryHDU((level + rng.normal(0, 0.5, (64, 64)))
                    .astype(np.float32)).writeto(path)
    return _rec(path.stem, kind, path=str(path), **kw)


# A WIDENED OPERATOR TOLERANCE REACHES A FARTHER MASTER (#274, H3 review).
# ``reference_for``'s option 1 (the dark master for these settings) is asked
# with the OPERATOR's match tolerance, read live off the config
# (``_tolerance``); every case above either hands ``reference_for`` a ``tol``
# of its own or runs with the matcher's bare default, so none of them ever
# widened ``calibration.temp_tol_c`` and checked that a dark master a few
# degrees further out than the default then wins.

async def test_the_operators_widened_temperature_tolerance_reaches_the_dark_master(
        sim_hub, monkeypatch, tmp_path):
    """A dark master 4 C off the frame's own temperature: outside the
    matcher's bare default of 2 C, inside an operator tolerance of 5 C. Only
    ``failed_solve_error`` reads the operator's config (``_tolerance``);
    ``reference_for`` alone takes whatever ``tol`` it is handed. A bias
    master stands ready as the fallback a narrow tolerance would take
    instead, so the two paths disagree on the reference's KIND, not only its
    number.

    RED under mutant "defaults always" (``_tolerance`` returns
    ``MatchTolerance()`` without reading the config), observed verbatim:

        E   AssertionError: FailedSolveError('plate solve failed: Not enough stars. (no level check was possible: the frame reads darker than the no-light reference, so that reference does not describe it)')
        E   assert False
        E    +  where False = isinstance(FailedSolveError('plate solve failed: Not enough stars. (no level check was possible: the frame reads darker than the no-light reference, so that reference does not describe it)'), <class 'astrodeck.solve.light.NoLightError'>)
        E    +    where <class 'astrodeck.solve.light.NoLightError'> = light.NoLightError

    At default tolerance the dark master misses (4 C is outside 2 C), so the
    bias plus the dark's own scaling becomes the reference instead -- and that
    floor sits ABOVE the capped frame's level, which reads as "darker than the
    reference", not as no light at all.
    """
    from astrodeck.config import CalibrationConfig, config_store
    config_store.set_calibration(CalibrationConfig(temp_tol_c=5.0))
    sim_hub.master_library = _Library([
        _write_master(tmp_path, "BIAS", 240.0, temp=-10.0),
        _write_master(tmp_path, "DARK", 250.0, seconds=12.0, temp=14.5)])
    frame = _frame(_no_light_frame())
    e = await light.failed_solve_error(
        frame, SolveResult(False, message="Not enough stars."),
        prefix="plate solve failed", hub=sim_hub)
    assert isinstance(e, light.NoLightError), e
    assert e.reference_kind == light.DARK_MASTER, e.verdict
    assert "dark master dark_250" in e.verdict.evidence(), e.verdict.evidence()


# A NUMBER THAT IS NOT ONE (H3 review). ``classify`` compared a NaN level with
# its band, both comparisons came back False, and the verdict fell through to
# NO_LIGHT; a sensor temperature that reads NaN puts one there, because
# ``reference_for`` scales the dark current by it. And nothing held the NaN
# filter in ``frame_stats``, which is what keeps a float frame's or a float
# master's NaN pixels out of a median. These cases hold both.

def test_a_reference_that_is_not_a_number_is_no_verdict():
    """A reference whose level is NaN, or whose ceiling is (it was scaled
    from a NaN, so its floor is suspect too), supports no verdict: the lit
    sky frame and the capped frame both come back unknown, in words.

    RED under mutant "no finite check" (the ``isfinite`` guard in
    ``classify`` made ``if False:``), observed verbatim:

        E   AssertionError: (Reference(level=nan, sigma=1.0, source='an explicit reference', detail=''), LightVerdict(kind='no_light', median=288....band=3.0016854502447314, reference=Reference(level=nan, sigma=1.0, source='an explicit reference', detail=''), why=''))
        E   assert 'no_light' == 'unknown'
    """
    nan = float("nan")
    for ref in (light.Reference(level=nan),
                light.Reference(level=251.0, ceiling=nan)):
        for data in (_flat_sky_frame(), _no_light_frame()):
            v = light.classify(data, ref)
            assert v.kind == light.UNKNOWN, (ref, v)
            assert v.why == light.NOT_A_NUMBER_WHY, v
            assert _no_digits(v.why), v.why
            v.evidence()                      # must format, never raise


async def test_a_sensor_temperature_that_is_not_a_number_never_reads_as_no_light(
        tmp_path, bus_lines):
    """A camera whose temperature reads NaN, a bias and a dark at its readout
    on disk, and a lit, starless sky some 55 ADU over the bias. The doubling
    law scales the dark current by the NaN, so the reference is NaN, and the
    failure is no verdict in words, never "no light". Through
    ``failed_solve_error``, the call every solve path makes, so the evidence
    line of a NaN reference is shown to format too.

    RED under mutant "no finite check", observed verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True
    """
    hub = type("H", (), {"master_library": _Library([
        _write_master(tmp_path, "BIAS", 240.0, temp=-10.0),
        _write_master(tmp_path, "DARK", 246.0, seconds=60.0,
                      temp=-10.0)])})()
    frame = _frame(_flat_sky_frame(pedestal=260.0), temp=float("nan"))
    e = await light.failed_solve_error(
        frame, SolveResult(False, message="Not enough stars."),
        prefix="plate solve failed", hub=hub)
    assert not isinstance(e, light.NoLightError), e
    assert e.verdict.kind == light.UNKNOWN, e.verdict
    assert e.verdict.median - 240.0 > 40.0, f"premise: a lit frame: {e.verdict}"
    assert e.verdict.reference is not None and math.isnan(
        e.verdict.reference.level), (
            f"premise: the NaN temperature reached the reference: "
            f"{e.verdict.reference}")
    assert str(e) == ("plate solve failed: Not enough stars. (no level check "
                      f"was possible: {light.NOT_A_NUMBER_WHY})"), e
    assert _no_digits(str(e)), f"the failure carries a number: {e}"
    evidence = [m for lv, m, src in bus_lines
                if src == "solve" and "light check" in m]
    assert len(evidence) == 1, bus_lines
    assert not [m for lv, m, src in bus_lines if lv == "warning"], bus_lines


def test_nan_pixels_in_a_float_frame_or_master_are_left_out(tmp_path):
    """Float data with NaN pixels in it, as a driver's float frame or a float32
    master (``CalibrationLibrary.build`` writes them float32) can carry: the
    median is of the finite pixels, so the capped frame is still no light at
    its own level, the sky frame is still cloud, and a master's level is
    still its level.

    RED under mutant "NaN pixels kept" (``frame_stats``'s ``isfinite`` filter
    made ``if False:``), observed verbatim:

        E   AssertionError: LightVerdict(kind='unknown', median=nan, pixel_sigma=nan, band=None, reference=Reference(level=250.0, sigma=1.0, sourc...frame is not a number (its sensor temperature or a master's level did not read as one), so the level cannot be judged")
        E   assert ('unknown' == 'no_light'

    Before the finite check that verdict was NO_LIGHT with a NaN median, so
    the case asks for the median as well as the kind: it goes red under
    this mutant with or without the check.
    """
    rng = np.random.default_rng(9)

    def with_nans(a: np.ndarray) -> np.ndarray:
        f = a.astype(np.float32)
        f.flat[rng.choice(f.size, 50, replace=False)] = np.nan
        return f

    capped = light.classify(with_nans(_no_light_frame()), REF)
    assert (capped.kind == light.NO_LIGHT
            and capped.median == pytest.approx(251.0, abs=1.0)), capped
    sky = light.classify(with_nans(_flat_sky_frame()), REF)
    assert sky.kind == light.CLOUD, sky
    path = tmp_path / "dark_nan.fits"
    master = (250.0 + np.random.default_rng(7).normal(0, 0.5, (64, 64))
              ).astype(np.float32)
    master[0, :8] = np.nan
    fits.PrimaryHDU(master).writeto(path)
    level = light.master_level(_rec("dark_nan", "DARK", path=str(path)))
    assert level is not None and level == pytest.approx(250.0, abs=0.5), level


async def test_a_rendered_preview_is_never_judged(tmp_path):
    """A NINA preview is an 8-bit stretch; its levels are not ADU.

    RED under mutant "linearity not asked" (``judge_frame`` skips the
    ``data_is_linear`` check), observed verbatim:

        E   AssertionError: LightVerdict(kind='no_light', median=251.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=249.98464965820312, sigma=1.0, source='the dark master for these settings', detail='dark master dark_250'), why='')
        E   assert 'no_light' == 'unknown'
    """
    lib = _Library([_write_master(tmp_path, "DARK", 250.0)])
    v = light.judge_frame(_frame(_no_light_frame(), linear=False), lib)
    assert v.kind == light.UNKNOWN, v
    assert "rendered preview" in v.why


# ============================================================ the solve paths

class _NoSolve:
    """ASTAP on a covered optic, or under cloud: the same words."""
    name = "failing"

    async def solve(self, path, **kw):
        return SolveResult(False, message="Not enough stars.")


def _capped(hub, monkeypatch, data_for=_no_light_frame):
    """The imaging camera returns ``data_for()`` at whatever it was asked,
    and every solver fails the way ASTAP does."""
    import astrodeck.providers as providers
    cam = hub.require("camera")

    async def expose(seconds, gain, offset, binning=1, **kw):
        return _frame(data_for(), seconds=seconds, gain=gain, offset=offset,
                      binning=binning, temp=18.5)
    monkeypatch.setattr(cam, "expose", expose)
    monkeypatch.setattr(providers, "pick_solver", lambda h: _NoSolve())


def _dark_library(hub, tmp_path, seconds: float):
    hub.master_library = _Library([_write_master(
        tmp_path, "DARK", 250.0, seconds=seconds, temp=18.5)])


def _no_digits(msg: str) -> bool:
    return re.search(r"\d", msg) is None


async def test_solve_and_sync_says_no_light_for_a_capped_optic(
        sim_hub, monkeypatch, tmp_path, bus_lines):
    """The recovery solve's path, end to end: a capped frame, a dark master
    for its settings, ASTAP's "Not enough stars." The failure is a
    ``NoLightError`` (a ``DeviceError``, so every caller still catches it),
    in words, and one info line carries the numbers.

    RED under mutant "solve_and_sync raises the plain DeviceError" (the call
    site restored to ``raise DeviceError(f"plate solve failed:
    {result.message}")``), observed verbatim:

        E   astrodeck.devices.base.DeviceError: plate solve failed: Not enough stars.
    """
    _capped(sim_hub, monkeypatch)
    _dark_library(sim_hub, tmp_path, 12.0)
    with pytest.raises(light.NoLightError) as e:
        await sim_hub.solve_and_sync(12.0)
    assert isinstance(e.value, DeviceError)
    msg = str(e.value)
    assert msg.startswith(f"plate solve failed: {light.NO_LIGHT_WORDS}"), msg
    assert "Not enough stars." in msg
    assert _no_digits(msg), f"the failure carries a number: {msg!r}"
    evidence = [m for lv, m, src in bus_lines
                if src == "solve" and "light check" in m]
    assert len(evidence) == 1, bus_lines
    assert "reference 250.0 ADU" in evidence[0], evidence


async def test_solve_and_sync_on_a_starless_sky_is_not_no_light(
        sim_hub, monkeypatch, tmp_path):
    """Control: the same path under overcast keeps the solver's words at the
    front, adds that light reached the sensor, and is not a
    ``NoLightError``."""
    _capped(sim_hub, monkeypatch, data_for=_flat_sky_frame)
    _dark_library(sim_hub, tmp_path, 12.0)
    with pytest.raises(light.FailedSolveError) as e:
        await sim_hub.solve_and_sync(12.0)
    assert not isinstance(e.value, light.NoLightError)
    assert e.value.verdict.kind == light.CLOUD
    assert str(e.value).startswith("plate solve failed: Not enough stars. "), e


async def test_solve_and_sync_over_a_bias_alone_does_not_say_light_is_reaching(
        sim_hub, monkeypatch, tmp_path):
    """The #251 scenario end to end with the least library #262 asks for: a
    capped frame, a bias master on disk and nothing else. The recovery solve's
    failure keeps today's words, says no level check could tell, and never
    claims light is reaching the sensor, which was the opposite of the truth.

    RED under mutant "the ceiling ignored", observed verbatim:

        E       AssertionError: LightVerdict(kind='cloud', median=251.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=239.98...the doubling law allows', detail='bias master bias_240; no dark to scale from, so no dark current is assumed'), why='')
        E       assert 'cloud' == 'unknown'

    Also RED, with the same failure, under "a bias alone has a ceiling at
    the bias".
    """
    _capped(sim_hub, monkeypatch)
    sim_hub.master_library = _Library([_write_master(
        tmp_path, "BIAS", 240.0, temp=-10.0)])
    with pytest.raises(light.FailedSolveError) as e:
        await sim_hub.solve_and_sync(12.0)
    assert not isinstance(e.value, light.NoLightError)
    assert e.value.verdict.kind == light.UNKNOWN, e.value.verdict
    assert str(e.value) == (
        "plate solve failed: Not enough stars. (no level check was possible: "
        f"{light.BELOW_THE_CEILING_WHY})"), e
    assert _no_digits(str(e.value)), e


async def test_solve_and_sync_with_no_library_keeps_todays_wording(
        sim_hub, monkeypatch):
    """Control: no reference, no verdict, and today's words, with the clause
    that says no level check was possible. The capped frame is exactly the
    frame a mutant would call no light.

    RED under mutant "no reference counts as no light", observed verbatim:

        E   AssertionError: assert not True
        E   +  where True = isinstance(NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)'), <class 'astrodeck.solve.light.NoLightError'>)
    """
    _capped(sim_hub, monkeypatch)
    assert getattr(sim_hub, "master_library", None) is None, "premise"
    with pytest.raises(DeviceError) as e:
        await sim_hub.solve_and_sync(12.0)
    assert not isinstance(e.value, light.NoLightError)
    assert str(e.value) == ("plate solve failed: Not enough stars. (no level "
                            "check was possible: no calibration library is "
                            "loaded)"), e


async def test_a_starfield_solves_and_is_never_classified(sim_hub,
                                                          monkeypatch):
    """A frame that solves is never judged: the classifier is for failures,
    and a solve path that asked it of every frame would pay a full-frame
    median on every centring attempt for nothing.

    RED under mutant "classify before the success check" (``solve_and_sync``
    calls ``failed_solve_error`` ahead of ``if not result.success``),
    observed verbatim:

        E   AssertionError: a frame that solved was classified: 1
    """
    asked = []
    real = light.failed_solve_error

    async def spy(*a, **kw):
        asked.append(a)
        return await real(*a, **kw)
    monkeypatch.setattr(light, "failed_solve_error", spy)
    out = await sim_hub.solve_and_sync(0.05)
    assert out["solver"] == "Simulator", out
    assert asked == [], f"a frame that solved was classified: {len(asked)}"


async def test_the_rotator_sync_says_no_light(sim_hub, monkeypatch,
                                              tmp_path):
    """RED under mutant "sync_rotator_to_sky raises the plain DeviceError",
    observed verbatim:

        E   astrodeck.devices.base.DeviceError: rotator sync: plate solve failed: Not enough stars.
    """
    _capped(sim_hub, monkeypatch)
    _dark_library(sim_hub, tmp_path, 12.0)
    with pytest.raises(light.NoLightError) as e:
        await sim_hub.sync_rotator_to_sky(exposure_s=12.0)
    assert str(e.value).startswith(
        f"rotator sync: plate solve failed: {light.NO_LIGHT_WORDS}"), e


async def test_the_rotate_loop_says_no_light(sim_hub, monkeypatch, tmp_path):
    """RED under mutant "_rotate_to_pa_attempts raises the plain
    DeviceError", observed verbatim:

        E   astrodeck.devices.base.DeviceError: rotate: plate solve failed: Not enough stars.
    """
    _capped(sim_hub, monkeypatch)
    _dark_library(sim_hub, tmp_path, 12.0)
    with pytest.raises(light.NoLightError) as e:
        await sim_hub.rotate_to_pa(90.0, exposure_s=12.0)
    assert str(e.value).startswith(
        f"rotate: plate solve failed: {light.NO_LIGHT_WORDS}"), e


async def test_polar_alignment_says_no_light(sim_hub, monkeypatch, tmp_path):
    """RED under mutant "polar _capture_and_solve raises the plain
    DeviceError", observed verbatim:

        E   astrodeck.devices.base.DeviceError: polar plate solve failed: Not enough stars.
    """
    from astrodeck.polar import native
    _capped(sim_hub, monkeypatch)
    session = type("S", (), {"solve_settings": {
        "exposure_s": 12.0, "gain": 200, "offset": 30, "binning": 2,
        "filter": None}})()
    _dark_library(sim_hub, tmp_path, 12.0)
    with pytest.raises(light.NoLightError) as e:
        await native._capture_and_solve(sim_hub, _NoSolve(), session)
    assert str(e.value).startswith(
        f"polar plate solve failed: {light.NO_LIGHT_WORDS}"), e


class _MainFailsGuideSolves:
    """ASTAP, scripted so only the IMAGING frame's solve fails: the guide
    camera's path (#264's control) must keep today's bare wording, never the
    light check, which has no reference for it."""
    name = "half-failing"

    async def solve(self, path, **kw):
        if "guide_offset_guide" in str(path):
            return SolveResult(True, ra_hours=5.5, dec_deg=-5.0,
                               rotation_deg=10.0, pixel_scale_arcsec=1.5,
                               message="guide ok")
        return SolveResult(False, message="Not enough stars.")


async def test_measure_guide_offset_says_no_light_for_a_capped_main_frame(
        sim_hub, monkeypatch, tmp_path):
    """#264. ``measure_guide_offset`` solves its own imaging frame and used to
    report a failed MAIN-frame solve as ``result.message`` with no light
    check -- the #251 class, on a path #251's own fix never reached because
    #251 only routed paths that RAISE and this one returns its result. It now
    asks the classifier, like every other solve path that exposes its own
    frame, and the words come back in "reason".

    RED under mutant "the site keeps result.message" (the main-frame branch
    reverted to the old unconditional ``out["reason"] = (f"the {which} frame
    did not solve, so there is no pair to difference: {out[which]['message']}")``),
    observed verbatim:

        E   AssertionError: {'camera': 'Sim Camera 533MM', ... 'main': {...
            'message': 'Not enough stars.', 'ok': False, ...}, ...}
        E   assert False
        E    +  where False = <built-in method startswith of str object at ...>('guide-scope offset: plate solve failed: no light: the optic is capped, covered or obstructed')
        E    +    where <built-in method startswith of str object at ...> = 'the main frame did not solve, so there is no pair to difference: Not enough stars.'.startswith
    """
    import astrodeck.providers as providers

    async def capped_expose(seconds, gain, offset, binning=1, **kw):
        return _frame(_no_light_frame(), seconds=seconds, gain=gain,
                     offset=offset, binning=binning, temp=18.5)
    cam = sim_hub.require("camera")
    monkeypatch.setattr(cam, "expose", capped_expose)
    monkeypatch.setattr(providers, "pick_solver",
                        lambda h: _MainFailsGuideSolves())
    _dark_library(sim_hub, tmp_path, 0.05)

    out = await sim_hub.measure_guide_offset(exposure_s=0.05,
                                             guide_exposure_s=0.05)

    assert out["offset"] is None, out
    assert out["reason"].startswith(
        f"guide-scope offset: plate solve failed: {light.NO_LIGHT_WORDS}"), out
    assert "Not enough stars." in out["reason"], out
    assert _no_digits(out["reason"]), f"the failure carries a number: {out}"


async def test_measure_guide_offset_keeps_todays_wording_for_the_guide_camera(
        sim_hub, monkeypatch, tmp_path):
    """Control for the test above: when the MAIN frame solves and the GUIDE
    frame fails, "reason" keeps the bare wording #264 left alone -- there is
    no no-light reference for the guide camera (the dark library keys masters
    on readout, not camera), so this path is not on trial here."""
    class _GuideFailsMainSolves:
        name = "half-failing"

        async def solve(self, path, **kw):
            if "guide_offset_guide" in str(path):
                return SolveResult(False, message="Not enough stars.")
            return SolveResult(True, ra_hours=5.5, dec_deg=-5.0,
                               rotation_deg=10.0, pixel_scale_arcsec=1.5,
                               message="main ok")

    import astrodeck.providers as providers
    monkeypatch.setattr(providers, "pick_solver",
                        lambda h: _GuideFailsMainSolves())

    out = await sim_hub.measure_guide_offset(exposure_s=0.05,
                                             guide_exposure_s=0.05)

    assert out["offset"] is None, out
    assert out["reason"] == ("the guide frame did not solve, so there is no "
                             "pair to difference: Not enough stars."), out


# ============================================================ the scan

ROOT = Path(__file__).resolve().parents[1] / "astrodeck"

#: The files whose solve paths expose their own frame.
SCANNED = ("hub.py", "polar/native.py")

CLASSIFIER = "failed_solve_error"

#: Solve call sites that never raise on a failed solve, with why. Each is
#: checked to still raise nothing under a failed-solve test, so an entry here
#: cannot hide a path that grows one. (A precondition raise, such as
#: ``measure_guide_offset``'s "no guide camera is connected", is not a failed
#: solve and is not this gate's business.)
#:
#: ``hub.py:Hub.measure_guide_offset`` USED TO BE HERE (#264): it reported a
#: failed imaging-frame solve as a bare ``result.message`` with no light
#: check, the #251 class on a path #251's own fix never reached. It is off
#: this list now that its main-frame failure asks ``failed_solve_error`` for
#: the words it puts in "reason" -- a RESULT-RETURNING use of the classifier,
#: never raised, which is why ``_scan_tree`` below counts a classifier call on
#: its own, not only one that is raised.
NEVER_RAISES: dict[str, str] = {
    "hub.py:Hub._solve_and_stamp":
        "the per-frame WCS job: a failed solve stamps nothing, and the job "
        "never raises; a light saved without WCS is the whole of its failure.",
    "hub.py:Hub.measure_guide_offset._solve_guide_frame":
        "the GUIDE camera's frame: the dark library holds the imaging "
        "camera's masters and does not key them on the camera, so there is no "
        "reference for it; and it returns the result, never raises.",
}

#: The call sites that ask the classifier on a failed solve and were routed
#: when this test was written -- by raising its answer, except
#: ``measure_guide_offset`` (#264), which returns it. The KNOWN POSITIVE: if
#: the scan stops seeing any of them it has gone blind, and the gate passes on
#: nothing.
KNOWN_CLASSIFIED = {
    "hub.py:Hub.solve_and_sync",
    "hub.py:Hub.sync_rotator_to_sky",
    "hub.py:Hub._rotate_to_pa_attempts",
    "hub.py:Hub.measure_guide_offset",
    "polar/native.py:_capture_and_solve",
}

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _is_solve_call(node: ast.AST) -> bool:
    """``solver.solve(...)``, as the acceptance names it, and any other
    ``<x>.solve(...)`` a new caller might spell."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "solve")


def _is_classifier_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Attribute) and f.attr == CLASSIFIER)
            or (isinstance(f, ast.Name) and f.id == CLASSIFIER))


def _walk_own(nodes):
    """Every node under ``nodes``, not descending into nested scopes."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _is_failed_solve_test(test: ast.AST) -> bool:
    """``not <...>.success ...``: how every path here spells a failed solve,
    including ``not (main.success and guide.success)``."""
    return (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and any(isinstance(n, ast.Attribute) and n.attr == "success"
                    for n in ast.walk(test.operand)))


def _functions(tree: ast.AST, prefix: str = ""):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = f"{prefix}{node.name}"
            yield q, node
            yield from _functions(node, f"{q}.")
        else:
            yield from _functions(node, prefix)


def _scan_tree(tree: ast.AST, rel: str) -> dict[str, dict]:
    """``{"rel:qualname": {...}}`` for every function whose own body calls
    ``.solve(``: its solve lines, and what happens under a failed-solve test,
    split into "classified" (the classifier was asked) and "bare" (something
    else was raised instead).

    "CLASSIFIED" IS A CALL, NOT ONLY A RAISE (#264). Most solve paths raise
    the classifier's answer straight away (``raise await
    _light.failed_solve_error(...)``); ``measure_guide_offset`` instead
    returns it (``err = await _light.failed_solve_error(...)``, ``out["reason"]
    = str(err)``), because this path's whole contract is a result from a lane,
    never an exception. Both ask the one question this gate exists to check
    for -- whether light reached the sensor -- so both count. "Bare" stays a
    RAISE that is not that call, which is the actual defect this gate was
    built to catch: a failed solve reported some other way, asking nothing.

    RED under mutant "classified only counts a raised classifier call" (the
    ``asked`` set narrowed back to ``{r.lineno for r in raises if r.exc is
    not None and _is_classifier_call(r.exc)}``, the pre-#264 definition),
    observed verbatim, from ``test_every_solve_that_raises_a_failure_asks_
    the_classifier``:

        E   AssertionError: these solve paths report a failed solve without asking astrodeck.solve.light whether light reached the sensor:
        E       hub.py:Hub.measure_guide_offset (solve on lines [6628]): neither raises a failed solve through the classifier nor is listed in NEVER_RAISES
    """
    out: dict[str, dict] = {}
    for qual, fn in _functions(tree):
        own = list(_walk_own(ast.iter_child_nodes(fn)))
        solves = sorted(n.lineno for n in own if _is_solve_call(n))
        if not solves:
            continue
        failure_bodies = [n.body for n in own
                          if isinstance(n, ast.If)
                          and _is_failed_solve_test(n.test)]
        raises = [r for body in failure_bodies for r in _walk_own(body)
                 if isinstance(r, ast.Raise)]
        asked = {n.lineno for body in failure_bodies for n in _walk_own(body)
                if _is_classifier_call(n)}
        out[f"{rel}:{qual}"] = {
            "solves": solves,
            "classified": sorted(asked),
            "bare": sorted(r.lineno for r in raises
                           if r.exc is None or not _is_classifier_call(r.exc)),
        }
    return out


def _scan() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for rel in SCANNED:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.update(_scan_tree(tree, rel))
    return found


def test_the_scan_counts_every_solver_solve_call():
    """Every ``solver.solve(`` in the two files, by text, is a call the AST
    scan sees: a spelling the parse misses cannot slip past the gate."""
    found = _scan()
    for rel in SCANNED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        by_text = len(re.findall(r"\bsolver\.solve\(", text))
        by_ast = sum(len(v["solves"]) for k, v in found.items()
                     if k.startswith(f"{rel}:"))
        assert by_ast == by_text, (rel, by_ast, by_text)


def test_every_solve_that_raises_a_failure_asks_the_classifier():
    """The gate. Every solve site either raises its failure through
    ``failed_solve_error`` and nothing else, or is on ``NEVER_RAISES``.

    RED under mutant "sync_rotator_to_sky raises the plain DeviceError",
    observed verbatim:

        E   AssertionError: these solve paths report a failed solve without asking astrodeck.solve.light whether light reached the sensor:
        E   hub.py:Hub.sync_rotator_to_sky (solve on lines [6167]): raises a failed solve without the classifier on lines [6175]
    """
    found = _scan()
    bad = {}
    for key, v in sorted(found.items()):
        if key in NEVER_RAISES:
            continue
        if v["bare"]:
            bad[key] = f"raises a failed solve without the classifier on " \
                       f"lines {v['bare']}"
        elif not v["classified"]:
            bad[key] = ("neither raises a failed solve through the classifier "
                        "nor is listed in NEVER_RAISES")
    assert not bad, (
        "these solve paths report a failed solve without asking "
        "astrodeck.solve.light whether light reached the sensor:\n"
        + "\n".join(f"  {k} (solve on lines {found[k]['solves']}): {why}"
                    for k, why in bad.items())
        + "\n\nRaise `await _light.failed_solve_error(frame, result, "
          "prefix=..., hub=...)`, or list the path in NEVER_RAISES with why.")


def test_the_allowlist_does_not_rot():
    """An entry that no longer solves, or that raises now, is a claim nobody
    is checking.

    RED under mutant "the WCS job raises on a failed solve" (``raise
    DeviceError(res.message)`` added under ``if not res.success`` in
    ``_solve_and_stamp``), observed verbatim:

        E   AssertionError: listed as never raising, but they raise now: {'hub.py:Hub._solve_and_stamp': [3508]}. Route them through the classifier and take them off NEVER_RAISES.
    """
    found = _scan()
    gone = sorted(set(NEVER_RAISES) - set(found))
    assert not gone, f"listed as never raising, but no longer a solve site: " \
                     f"{gone}"
    raising = {k: found[k]["classified"] + found[k]["bare"]
               for k in NEVER_RAISES
               if found[k]["classified"] or found[k]["bare"]}
    assert not raising, (f"listed as never raising, but they raise now: "
                         f"{raising}. Route them through the classifier and "
                         f"take them off NEVER_RAISES.")


def test_the_scan_still_sees_the_known_solve_paths():
    """The known positive.

    RED under mutant "_is_solve_call matches nothing", observed verbatim:

        E   AssertionError: the scan no longer sees these solve paths: ['hub.py:Hub._rotate_to_pa_attempts', 'hub.py:Hub.solve_and_sync', 'hub.py:Hub.sync_rotator_to_sky', 'polar/native.py:_capture_and_solve']
    """
    found = _scan()
    missing = sorted(KNOWN_CLASSIFIED - set(found))
    assert not missing, f"the scan no longer sees these solve paths: {missing}"
    unclassified = sorted(k for k in KNOWN_CLASSIFIED
                          if not found[k]["classified"])
    assert not unclassified, f"known paths stopped classifying: {unclassified}"
