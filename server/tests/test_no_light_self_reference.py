"""The no-light check references itself when the library has nothing (#262).

``astrodeck.solve.light`` judges a failed solve's frame against the median
this camera reads with no light on it: a dark master at the frame's settings,
or a bias master plus the least dark current the doubling law allows. On the
rig nothing had shot a bias or a dark at the solve's readout, so the check had
no reference, gave no verdict, and a capped optic went on reading as "not
enough stars" (#251, #262).

So when the library holds neither a dark master for the frame nor a bias at
its readout, ``failed_solve_error`` shoots one frame itself (#262, S2
orchestrator ruling 2, owner list item 20): at the camera's shortest
exposure, at the failed frame's gain, offset and binning, shutter closed,
under the hub's exposure guard and bounded. At that exposure a sensor reads
its bias pedestal whether or not light reaches it, so the frame stands in for
the missing bias master, through the existing bias branch: the floor is the
self-shot plus the least dark current the doubling law allows (from a dark at
that readout, when the library has one), the ceiling the most it allows, or
none without a dark. The self-shot is kept for the night, keyed on the camera,
the readout, a 2 C sensor-temperature band and the night.

These tests hold the verdicts it gives on synthetic frames, the cache, the
self-shots that fail, the four solve paths that ask, and a cancel landing
while it is exposing.

Each case names the mutation that turns it red and the failure it produced,
verbatim, from a run of that mutant in a private scratch copy of server/.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from _deadline import wait_until
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.calibration.matcher import MasterRecord, MatchTolerance
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.hub import Hub
from astrodeck.solve import light
from astrodeck.solve.base import SolveResult

#: Big enough that a median is a median, small enough to be quick.
SHAPE = (480, 640)

#: The night the cases run on, unless a case moves it.
NIGHT = "2026-09-25"

#: What a failed solve says today when the library holds nothing at the
#: #251 frame's readout (12 s, gain 200, offset 30, bin 2). A self-shot that
#: gives no reference keeps these words and adds why.
TODAY = ("plate solve failed: Not enough stars. (no level check was possible: "
         "the calibration library has no dark master for this frame's "
         "exposure, gain 200, offset 30, bin 2 and temperature, and no bias "
         "master at gain 200, offset 30, bin 2")


@pytest.fixture(autouse=True)
def _a_fresh_night(monkeypatch):
    """No case inherits another's self-reference, and every case runs on a
    fixed night unless it moves it."""
    light._SELF_REFERENCES.clear()
    monkeypatch.setattr(light, "night_key", lambda ts=None: NIGHT)
    yield
    light._SELF_REFERENCES.clear()


def _dark(level: float, sigma: float = 9.0, hot_frac: float = 0.006,
          seed: int = 262) -> np.ndarray:
    """A frame with no light on it, shaped like #251's: a Gaussian core at
    ``level`` (sigma 9.0) and the warm pixels of an 18.5 C sensor."""
    rng = np.random.default_rng(seed)
    a = rng.normal(level, sigma, SHAPE)
    n_hot = int(hot_frac * a.size)
    idx = rng.choice(a.size, n_hot, replace=False)
    a.flat[idx] = level + rng.uniform(150.0, 600.0, n_hot)
    return np.clip(np.rint(a), 0, 65535).astype(np.uint16)


def _sky(pedestal: float = 251.0, sky: float = 40.0,
         seed: int = 263) -> np.ndarray:
    """The same sensor under a starless overcast ``sky`` ADU deep, with its
    own shot noise: light reached it, and a solver says "not enough stars"."""
    rng = np.random.default_rng(seed)
    base = _dark(pedestal, seed=seed).astype(np.float64)
    a = base + sky + rng.normal(0.0, np.sqrt(sky), SHAPE)
    return np.clip(np.rint(a), 0, 65535).astype(np.uint16)


def _frame(data: np.ndarray, *, seconds: float = 12.0, gain: int = 200,
           offset: int = 30, binning: int = 2, temp: float | None = 18.5,
           linear: bool = True) -> CameraFrame:
    return CameraFrame(data=data, exposure_s=seconds, gain=gain, offset=offset,
                       binning=binning, bayer_pattern=None, temperature_c=temp,
                       timestamp=0.0, data_is_linear=linear)


class _Sensor:
    """A shutterless CMOS camera with its optic capped: every exposure reads
    the bias pedestal plus the dark current for its length, whatever
    ``light`` says, since there is no shutter to close. ``sky_rate`` uncaps
    it: that many ADU/s of light reach the sensor, shutter-closed exposures
    included, for the same reason. ``fault`` breaks the shutter-closed
    exposures only: ``"raises"``, ``"hangs"`` (past any bound a case sets),
    ``"constant"`` (a buffer) or ``"rendered"`` (a stretched preview).
    ``calls`` records every exposure, ``shots`` the self-shots."""

    def __init__(self, *, bias: float = 240.0, dark_rate: float = 0.0,
                 name: str = "test camera", temp: float = 18.5,
                 min_exposure_s: float | None = None,
                 fault: str | None = None, sky_rate: float = 0.0):
        self.name = name
        self.connected = True
        self.bias, self.dark_rate, self.temp = bias, dark_rate, temp
        self.sky_rate = sky_rate
        if min_exposure_s is not None:
            self.min_exposure_s = min_exposure_s
        self.fault = fault
        self.calls: list[tuple] = []
        self.cancelled = 0

    @property
    def shots(self) -> list[tuple]:
        return [c for c in self.calls if not c[4]]

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target=""):
        self.calls.append((seconds, gain, offset, binning, light))
        fault = None if light else self.fault
        if fault == "raises":
            raise DeviceError("imageready timeout")
        if fault == "hangs":
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        level = self.bias + (self.dark_rate + self.sky_rate) * seconds
        data = (np.full(SHAPE, int(round(level)), np.uint16)
                if fault == "constant" else _dark(level, seed=len(self.calls)))
        return CameraFrame(data=data, exposure_s=seconds, gain=gain,
                           offset=offset, binning=binning, bayer_pattern=None,
                           temperature_c=self.temp, timestamp=0.0,
                           data_is_linear=fault != "rendered")

    async def abort_exposure(self):
        pass


class _Hub:
    """The parts of a hub ``failed_solve_error`` reads: the library, the
    camera, and the REAL ``Hub.exposure_guard`` over a lock of its own."""
    exposure_guard = Hub.exposure_guard

    def __init__(self, library, cam):
        self.master_library = library
        self.devices = {"camera": cam} if cam is not None else {}
        self._capture_lock = asyncio.Lock()
        self._capture_busy = None


class _Library:
    def __init__(self, masters):
        self._masters = masters

    def list_masters(self):
        return list(self._masters)


def _master(tmp_path: Path, kind: str, level: float, *, seconds: float = 12.0,
            gain: int = 200, offset: int = 30, temp: float | None = 18.5,
            binning: int = 2) -> MasterRecord:
    """A master on disk, as ``CalibrationLibrary.build`` writes one: float32
    pixels around ``level``."""
    path = tmp_path / f"{kind.lower()}_{level:g}_g{gain}_b{binning}.fits"
    rng = np.random.default_rng(7)
    fits.PrimaryHDU((level + rng.normal(0, 0.5, (64, 64)))
                    .astype(np.float32)).writeto(path)
    return MasterRecord(id=path.stem, frame_type=kind,
                        exposure_s=0.0 if kind == "BIAS" else seconds,
                        gain=gain, offset=offset, temp_c=temp, binning=binning,
                        filter="", frame_count=20, path=str(path),
                        built_ts=1.0)


def _elsewhere(tmp_path: Path) -> _Library:
    """A library that holds a bias, at another gain: nothing at the failed
    frame's readout, which is the library the rig has (#262)."""
    return _Library([_master(tmp_path, "BIAS", 240.0, gain=150)])


async def _fail(hub, frame) -> light.FailedSolveError:
    return await light.failed_solve_error(
        frame, SolveResult(False, message="Not enough stars."),
        prefix="plate solve failed", hub=hub)


def _evidence(bus_lines) -> list[str]:
    return [m for lv, m, src in bus_lines
            if src == "solve" and "light check" in m]


# ============================================================ the verdicts

@pytest.mark.parametrize("reported, shot_s", [(None, 0.001),
                                              (3.2e-5, 3.2e-5)])
async def test_a_capped_frame_at_the_self_shot_level_is_no_light(
        tmp_path, bus_lines, reported, shot_s):
    """The library holds nothing at the frame's readout. The self-shot reads
    251, and so does the capped frame: no light. The self-shot is taken once,
    at the camera's shortest exposure (0.001 s when it reports none), at the
    frame's gain, offset and binning, with the shutter closed, and the one
    evidence line names it.

    RED under mutant "no self-reference" (``failed_solve_error`` never shoots
    one, so a library with nothing at the readout is no reference): a
    ``FailedSolveError``, verdict UNKNOWN, in today's words. Observed
    verbatim, from a ``-vv`` run so the words are not cut short:

        E   AssertionError: FailedSolveError("plate solve failed: Not enough stars. (no level check was possible: the calibration library has no dark master for this frame's exposure, gain 200, offset 30, bin 2 and temperature, and no bias master at gain 200, offset 30, bin 2)")
        E   assert False

    RED under mutant "always the fallback" (``_min_exposure_s`` returns
    ``SELF_REFERENCE_FALLBACK_S`` whatever the camera reports), observed
    verbatim:

        E   AssertionError: [(0.001, 200, 30, 2, False)]
        E   assert [(0.001, 200, 30, 2, False)] == [(3.2e-05, 200, 30, 2, False)]
    """
    cam = _Sensor(bias=251.0, min_exposure_s=reported)
    hub = _Hub(_elsewhere(tmp_path), cam)
    e = await _fail(hub, _frame(_dark(251.0)))
    assert isinstance(e, light.NoLightError), e
    assert cam.shots == [(shot_s, 200, 30, 2, False)], cam.calls
    ev = _evidence(bus_lines)
    assert len(ev) == 1, bus_lines
    assert light.SELF_REFERENCE_WORDS in ev[0], ev
    assert "reference 251.0 ADU, with no ceiling" in ev[0], ev
    assert not hub._capture_lock.locked()


async def test_a_lit_frame_past_the_ceiling_with_a_dark_anchor_is_cloud(
        tmp_path, bus_lines):
    """A 60 s dark at -10 C at the frame's readout, reading 2 ADU over the
    240 ADU the self-shot reads, measures a dark current of 2/60 ADU/s. Scaled
    to the 12 s, 18.5 C frame it puts the floor at 240 + 0.4 x 2^(28.5/7) =
    246.73 and the ceiling at 240 + 0.4 x 2^(28.5/5) = 260.79 (the numbers
    ``test_failed_solve_says_no_light`` holds for a bias master). A starless
    overcast 40 ADU deep on a 251 ADU pedestal is past the ceiling: cloud.

    RED under mutant "no self-reference", observed verbatim:

        E   AssertionError: LightVerdict(kind='unknown', median=291.0, pixel_sigma=14.825999999999999, band=None, reference=None, why="the calibra...r this frame's exposure, gain 200, offset 30, bin 2 and temperature, and no bias master at gain 200, offset 30, bin 2")
        E   assert 'unknown' == 'cloud'

    RED under mutant "the self-shot ignores the dark anchor" (the
    self-reference branch of ``reference_for`` keeps ``dark_lo, dark_hi =
    0.0, math.inf`` whatever dark there is), observed verbatim:

        E   AssertionError: LightVerdict(kind='unknown', median=291.0, pixel_sigma=14.825999999999999, band=3.0016854502447314, reference=Referenc...er for this temperature, a level this far above the bias could be dark current alone, so light cannot be told from it')
        E   assert 'unknown' == 'cloud'
    """
    anchor = _master(tmp_path, "DARK", 242.0, seconds=60.0, temp=-10.0)
    lib = _Library([anchor, _master(tmp_path, "BIAS", 240.0, gain=150)])
    cam = _Sensor(bias=240.0)
    e = await _fail(_Hub(lib, cam), _frame(_sky(pedestal=251.0)))
    assert not isinstance(e, light.NoLightError), e
    v = e.verdict
    assert v.kind == light.CLOUD, v
    # The master's float32 pixels put its median a hair off 242, and the
    # self-shot's integer pixels put its median at 240 exactly.
    dark = (light.master_level(anchor) - 240.0) / 60.0 * 12.0
    assert abs(dark - 0.4) < 0.01, f"premise: the anchor's level: {dark}"
    assert v.reference.level == pytest.approx(
        240.0 + dark * 2 ** (28.5 / 7), abs=1e-6), v.reference
    assert v.reference.ceiling == pytest.approx(
        240.0 + dark * 2 ** (28.5 / 5), abs=1e-6), v.reference.ceiling
    assert v.median - v.reference.ceiling > 25.0, f"premise: past it: {v}"
    assert len(cam.shots) == 1, cam.calls
    assert light.SELF_REFERENCE_WORDS in _evidence(bus_lines)[0]


async def test_a_warm_capped_frame_over_the_self_shot_bias_is_no_verdict(
        tmp_path, bus_lines):
    """The control, #251's own shape: a capped, shutterless sensor at 18.5 C
    with 11 ADU of dark current in the 12 s frame over its 240 ADU bias, and
    no dark in the library to measure a rate from. The self-shot reads the
    bias, the frame reads 11 ADU over it, and 11 ADU may be dark current
    alone, so there is no verdict: never no light by default, and never cloud.

    RED under mutant "the self-shot at the frame's exposure" (the self-shot
    is taken at ``frame.exposure_s``, which on a shutterless sensor is a dark,
    capped or not, and under an open optic a picture of the sky), observed
    verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True

    RED under mutant "the self-shot is the level" (the self-reference branch
    returns ``Reference(level=self_shot)`` with no ceiling, as if it were a
    dark master for the frame), observed verbatim:

        E   AssertionError: LightVerdict(kind='cloud', median=251.0, pixel_sigma=8.8956, band=3.0006068711505556, reference=Reference(level=240.0,...rame that stands in for a bias', detail='self-reference 0.001 s at gain 200, offset 30, bin 2, shot just now'), why='')
        E   assert 'cloud' == 'unknown'
    """
    cam = _Sensor(bias=240.0, dark_rate=11.0 / 12.0)
    failed = await cam.expose(12.0, 200, 30, 2)
    e = await _fail(_Hub(_elsewhere(tmp_path), cam), failed)
    assert not isinstance(e, light.NoLightError), e
    v = e.verdict
    assert v.median - v.reference.level == pytest.approx(11.0, abs=0.5), v
    assert v.kind == light.UNKNOWN, v
    assert v.why == light.BELOW_THE_CEILING_WHY, v
    assert "light is reaching the sensor" not in str(e), e
    assert cam.shots == [(0.001, 200, 30, 2, False)], cam.calls


async def test_no_library_takes_no_self_shot(bus_lines):
    """Control: the self-shot stands in for a master the library LACKS, so a
    hub with no library loaded (a bare hub; the app always loads one) takes
    none, and keeps today's words. The camera would read the frame's level.

    RED under mutant "a self-shot with no library" (``failed_solve_error``
    shoots one whenever there is no reference), observed verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True
    """
    cam = _Sensor(bias=251.0)
    e = await _fail(_Hub(None, cam), _frame(_dark(251.0)))
    assert not isinstance(e, light.NoLightError), e
    assert cam.shots == [], cam.calls
    assert str(e) == ("plate solve failed: Not enough stars. (no level check "
                      "was possible: no calibration library is loaded)"), e


# ============================================================ the cache

def test_the_temperature_band_is_the_matchers_tolerance():
    """The band the cache keys on is the matcher's default temperature
    tolerance, 2 C: a self-shot is reused across the same span a dark master
    is accepted across.

    RED under mutant "a band of 5 C", observed verbatim:

        E   assert 5.0 == 2.0
        E    +  where 5.0 = light.SELF_REFERENCE_BAND_C
    """
    assert light.SELF_REFERENCE_BAND_C == MatchTolerance().temp_tol_c == 2.0


@pytest.mark.parametrize("change, shots", [
    ("nothing", 1),
    ("temperature within the band", 1),
    ("temperature band", 2),
    ("night", 2),
    ("camera", 2),
    ("gain", 2),
    ("offset", 2),
    ("binning", 2),
])
async def test_the_self_reference_is_kept_for_the_night(tmp_path, monkeypatch,
                                                        bus_lines, change,
                                                        shots):
    """Two failed solves. The second takes no frame when it shares the first
    one's camera, gain, offset, binning, 2 C temperature band (18.5 and 19.5
    C are both in 18 to 20) and night; any one of them changed takes one.

    RED under mutant "no cache" (nothing is remembered), observed verbatim:

        E   AssertionError: ('nothing', [(0.001, 200, 30, 2, False), (0.001, 200, 30, 2, False)])
        E   assert 2 == 1
        E   AssertionError: ('temperature within the band', [(0.001, 200, 30, 2, False), (0.001, 200, 30, 2, False)])
        E   assert 2 == 1

    RED under mutant "the band not in the key", observed verbatim:

        E   AssertionError: ('temperature band', [(0.001, 200, 30, 2, False)])
        E   assert 1 == 2

    RED under mutant "the night not in the key", observed verbatim:

        E   AssertionError: ('night', [(0.001, 200, 30, 2, False)])
        E   assert 1 == 2

    RED under mutant "the camera not in the key", observed verbatim:

        E   AssertionError: ('camera', [])
        E   assert 1 == 2
    """
    lib = _Library([_master(tmp_path, "BIAS", 240.0, gain=150)])
    cam = _Sensor(bias=251.0)
    hub = _Hub(lib, cam)
    first = await _fail(hub, _frame(_dark(251.0)))
    assert isinstance(first, light.NoLightError), first
    kw: dict = {}
    if change == "temperature within the band":
        kw["temp"] = 19.5
    elif change == "temperature band":
        kw["temp"] = 21.0
    elif change == "night":
        monkeypatch.setattr(light, "night_key", lambda ts=None: "2026-09-26")
    elif change == "camera":
        cam = _Sensor(bias=251.0, name="another camera")
        hub.devices["camera"] = cam
    elif change in ("gain", "offset", "binning"):
        kw[change] = {"gain": 100, "offset": 40, "binning": 1}[change]
    second = await _fail(hub, _frame(_dark(251.0), **kw))
    assert isinstance(second, light.NoLightError), second
    taken = len(cam.shots) if change != "camera" else 1 + len(cam.shots)
    assert taken == shots, (change, cam.calls)
    # The evidence line says which it was. RED under the verifier's mutant
    # "a kept one not marked kept" (the kept ``SelfBias`` returned as it
    # was stored), observed verbatim on "nothing" and on "temperature within
    # the band":
    #     E   AssertionError: ('nothing', ['self-reference 0.001 s at gain 200, offset 30, bin 2, shot just now; no dark to scale from, so no dark c...1 s at gain 200, offset 30, bin 2; no dark to scale from, so no dark current is assumed); band +/-3.00 ADU; no_light'])
    #     E   assert False == (1 == 1)
    ev = _evidence(bus_lines)
    assert len(ev) == 2 and ", shot just now;" in ev[0], (change, ev)
    marked = ", kept from earlier tonight;" in ev[1]
    assert marked == (shots == 1), (change, [e[e.find("self-reference"):]
                                             for e in ev])


async def test_a_bias_master_at_the_readout_takes_no_self_shot(tmp_path):
    """Control: with a bias master at the frame's readout the library has a
    reference, and no frame is taken. #251's capped frame over that bias
    alone is the no-verdict case ``test_failed_solve_says_no_light`` holds.

    RED under mutant "a self-shot on every failure" (the self-shot is taken
    whether or not the library had a reference), observed verbatim:

        E   AssertionError: [(0.001, 200, 30, 2, False)]
        E   assert [(0.001, 200, 30, 2, False)] == []
    """
    cam = _Sensor(bias=240.0)
    lib = _Library([_master(tmp_path, "BIAS", 240.0, temp=-10.0)])
    e = await _fail(_Hub(lib, cam), _frame(_dark(251.0)))
    assert cam.shots == [], cam.calls
    assert e.verdict.kind == light.UNKNOWN, e.verdict
    assert "bias master" in e.verdict.reference.detail, e.verdict.reference


async def test_only_tonights_self_references_are_held(tmp_path, monkeypatch):
    """A write drops every other night's self-references, so the cache holds
    one night at a time and never grows across a long uptime. Added by the
    verifier, whose mutant survived the suite as it was.

    RED under the verifier's mutant "other nights never dropped" (the drop
    loop in ``_remember`` deleted), observed verbatim:

        E   AssertionError: ['2026-09-25', '2026-09-26']
        E   assert ['2026-09-25', '2026-09-26'] == ['2026-09-26']
    """
    hub = _Hub(_elsewhere(tmp_path), _Sensor(bias=251.0))
    assert isinstance(await _fail(hub, _frame(_dark(251.0))),
                      light.NoLightError)
    monkeypatch.setattr(light, "night_key", lambda ts=None: "2026-09-26")
    assert isinstance(await _fail(hub, _frame(_dark(251.0))),
                      light.NoLightError)
    nights = sorted(k[-1] for k in light._SELF_REFERENCES)
    assert nights == ["2026-09-26"], nights


@pytest.mark.parametrize("sky, kept", [("dusk", False), ("a night sky", True)])
async def test_a_self_shot_beside_a_bright_frame_is_not_kept(
        tmp_path, bus_lines, sky, kept):
    """A 1 ms shot catches a few ADU of a bright sky. At dusk 6000 ADU/s
    reach the uncapped sensor: the failed 5 s frame reads 30 240, the
    self-shot 246 where the pedestal is 240. That frame is judged against the
    shot and gets no verdict, but the shot is not kept: an hour later a faint
    night sky of 0.5 ADU/s puts the 12 s frame at 246, and judged against the
    kept 246 it would be "no light: the optic is capped". It takes a fresh
    shot instead, which reads the pedestal, and the 6 ADU of sky are no
    verdict. The control: a shot beside a night sky of 3 ADU/s (the failed
    12 s frame 36 ADU over the pedestal) is kept, and the second failure
    takes no frame. Added by the verifier.

    RED under the verifier's mutant "a self-shot kept whatever the frame
    showed" (``_light_share`` returns 0.0), observed verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True

    RED under the verifier's mutant "a self-shot never kept beside light"
    (``SELF_REFERENCE_KEEP_LIGHT_ADU`` set to 0.0), observed verbatim on the
    control:

        E   AssertionError: ('a night sky', [(12.0, 200, 30, 2, True), (0.001, 200, 30, 2, False), (12.0, 200, 30, 2, True), (0.001, 200, 30, 2, False)])
        E   assert 2 == 1
    """
    rate, seconds = (6000.0, 5.0) if sky == "dusk" else (3.0, 12.0)
    cam = _Sensor(bias=240.0, sky_rate=rate)
    hub = _Hub(_elsewhere(tmp_path), cam)
    first = await _fail(hub, await cam.expose(seconds, 200, 30, 2))
    assert not isinstance(first, light.NoLightError), first
    assert first.verdict.kind == light.UNKNOWN, first.verdict
    shot = first.verdict.reference.level
    assert shot == (246.0 if sky == "dusk" else 240.0), \
        f"premise: the shot caught the sky's share: {shot}"
    cam.sky_rate = 0.5
    second = await _fail(hub, await cam.expose(12.0, 200, 30, 2))
    assert not isinstance(second, light.NoLightError), second
    assert len(cam.shots) == (1 if kept else 2), (sky, cam.calls)
    assert second.verdict.kind == light.UNKNOWN, second.verdict
    assert second.verdict.why == light.BELOW_THE_CEILING_WHY, second.verdict
    assert second.verdict.reference.level == 240.0, second.verdict.reference
    # The evidence line says a shot was not kept, and why, in words.
    ev = _evidence(bus_lines)
    assert (", shot just now and not kept:" in ev[0]) == (not kept), ev


# ============================================================ a failed self-shot

@pytest.mark.parametrize("fault, said", [
    ("raises", "failed"),
    ("hangs", "timed out"),
    ("constant", "read every pixel the same"),
    ("rendered", "not linear sensor data"),
    ("no camera", "no connected camera"),
    ("disconnected", "no connected camera"),
])
async def test_a_self_shot_that_fails_is_no_reference(tmp_path, monkeypatch,
                                                      bus_lines, fault, said):
    """A self-shot that raises, runs past its bound, returns a buffer or a
    rendered preview, or has no camera to take it, is no reference: never a
    crash of the solve path and never "no light". The capped frame reads 251,
    what the camera reads, so a self-shot used in spite of its fault would
    say no light. The failure keeps today's words and names the
    self-reference, in words only; the camera's own text goes to the
    evidence line. A failed self-shot is not kept, so the next failure takes
    another, and the guard is free after it.

    RED under mutant "no constant check on the self-shot", observed
    verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True

    RED under mutant "linearity not asked of the self-shot", observed
    verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True

    RED under mutant "an unbounded self-shot" (no ``wait_for``), observed
    verbatim (the case's own 2 s bound fires):

        astrodeck\\solve\\light.py:849: in failed_solve_error
        E   asyncio.exceptions.CancelledError
        E   TimeoutError

    RED under mutant "the self-shot's failure escapes" (its ``except
    Exception`` narrowed to ``TimeoutError``, so a camera fault reaches the
    level check's own guard), observed verbatim:

        E   AssertionError: plate solve failed: Not enough stars. (no level check was possible: the level check itself failed)
        E   assert False

    RED under mutant "a failed self-shot is kept" (the cache written with the
    failure, so the next failure reads it and takes no frame), observed
    verbatim:

        E   AssertionError: FailedSolveError("plate solve failed: Not enough stars. (no level check was possible: the calibration library has no d...s master at gain 200, offset 30, bin 2; the shortest-exposure frame that stands in for a bias failed earlier tonight)")
        E   assert False

    RED under the verifier's mutant "a disconnected camera still shoots"
    (``_self_reference`` asks only that a camera is there), on the
    ``disconnected`` case, observed verbatim:

        E   AssertionError: NoLightError('plate solve failed: no light: the optic is capped, covered or obstructed (the frame reads at the level this camera reads with no light on it; the solver said: Not enough stars.)')
        E   assert not True
    """
    monkeypatch.setattr(light, "SELF_REFERENCE_TIMEOUT_S", 0.2)
    if fault == "no camera":
        cam = None
    elif fault == "disconnected":
        # This fake would expose regardless; a real one raises.
        cam = _Sensor(bias=251.0)
        cam.connected = False
    else:
        cam = _Sensor(bias=251.0, fault=fault)
    hub = _Hub(_elsewhere(tmp_path), cam)
    e = await asyncio.wait_for(_fail(hub, _frame(_dark(251.0))), 2.0)
    assert not isinstance(e, light.NoLightError), e
    assert isinstance(e, light.FailedSolveError), e
    assert e.verdict.kind == light.UNKNOWN, e.verdict
    msg = str(e)
    assert msg.startswith(TODAY + "; "), msg
    assert light.SELF_REFERENCE_WORDS in msg and said in msg, msg
    clause = msg[len(TODAY):]
    assert re.search(r"\d", clause) is None, f"a number in {clause!r}"
    assert "imageready" not in msg, msg
    ev = _evidence(bus_lines)
    assert len(ev) == 1 and light.SELF_REFERENCE_WORDS in ev[0], bus_lines
    if fault == "raises":
        assert "the camera said: imageready timeout" in ev[0], ev
    assert not [m for lv, m, src in bus_lines if lv == "warning"], bus_lines
    assert not hub._capture_lock.locked()
    if cam is None:
        return
    if fault == "hangs":
        assert cam.cancelled == 1, "the bound did not cancel the exposure"
    if fault == "disconnected":
        assert cam.calls == [], cam.calls
    # The next failure, with the camera working again, takes a frame.
    cam.fault = None
    cam.connected = True
    again = await _fail(hub, _frame(_dark(251.0)))
    assert isinstance(again, light.NoLightError), again
    assert len(cam.shots) == (1 if fault == "disconnected" else 2), cam.calls


async def test_a_busy_camera_refuses_the_self_shot(tmp_path, bus_lines):
    """The self-shot goes through the hub's exposure guard, which refuses
    rather than queue: while another path exposes, no self-shot is taken, and
    the failure is no verdict that says the self-reference was not had.

    RED under mutant "no guard" (the self-shot calls ``cam.expose`` without
    ``exposure_guard``), observed verbatim:

        E   AssertionError: [(0.001, 200, 30, 2, False)]
        E   assert [(0.001, 200, 30, 2, False)] == []
    """
    cam = _Sensor(bias=251.0)
    hub = _Hub(_elsewhere(tmp_path), cam)
    async with hub.exposure_guard("capture light"):
        e = await _fail(hub, _frame(_dark(251.0)))
    assert cam.shots == [], cam.calls
    assert not isinstance(e, light.NoLightError), e
    assert e.verdict.kind == light.UNKNOWN, e.verdict
    ev = _evidence(bus_lines)
    assert "the camera said: camera is busy (capture light)" in ev[0], ev


# ============================================================ the solve paths

def _capped_rig(hub, monkeypatch, tmp_path) -> list[tuple]:
    """The imaging camera is capped: every exposure, lit or not, reads 251.
    Every solver fails as ASTAP does, and the library holds a bias at another
    gain only. Returns the camera's calls."""
    import astrodeck.providers as providers
    calls: list[tuple] = []
    cam = hub.require("camera")

    async def expose(seconds, gain, offset, binning=1, light=True, **kw):
        calls.append((seconds, gain, offset, binning, light))
        return _frame(_dark(251.0, seed=len(calls)), seconds=seconds,
                      gain=gain, offset=offset, binning=binning)
    monkeypatch.setattr(cam, "expose", expose)

    class _NoSolve:
        name = "failing"

        async def solve(self, path, **kw):
            return SolveResult(False, message="Not enough stars.")
    monkeypatch.setattr(providers, "pick_solver", lambda h: _NoSolve())
    hub.master_library = _elsewhere(tmp_path)
    return calls


@pytest.mark.parametrize("path, prefix, readout", [
    ("solve_and_sync", "plate solve failed", (200, 30, 2)),
    ("sync_rotator_to_sky", "rotator sync: plate solve failed", (200, 30, 2)),
    ("rotate_to_pa", "rotate: plate solve failed", (200, 30, 2)),
    ("polar", "polar plate solve failed", (120, 20, 1)),
])
async def test_every_solve_path_takes_the_self_shot_with_the_guard_free(
        sim_hub, monkeypatch, tmp_path, path, prefix, readout):
    """The four paths that expose their own frame and raise on a failed
    solve (``hub.solve_and_sync``, ``sync_rotator_to_sky``,
    ``_rotate_to_pa_attempts`` via ``rotate_to_pa``, and polar's
    ``_capture_and_solve``) each release the exposure guard before they ask
    ``failed_solve_error``, so the self-shot is taken and the capped optic is
    named. Polar solves at its own readout, and the self-shot follows the
    failed frame's. Bounded, so a path that waited on its own guard would
    fail here rather than hang the suite.

    RED under mutant "solve_and_sync judges inside a guard" (hub.py, in the
    scratch copy: the ``raise await _light.failed_solve_error(...)`` wrapped
    in ``async with self.exposure_guard("plate solve")``), observed verbatim:

        E   astrodeck.solve.light.FailedSolveError: plate solve failed: Not enough stars. (no level check was possible: the calibration library has no dark master for this frame's exposure, gain 200, offset 30, bin 2 and temperature, and no bias master at gain 200, offset 30, bin 2; the shortest-exposure frame that stands in for a bias failed)

    RED under mutant "the self-shot at the solve's fixed readout" (gain 200,
    offset 30, bin 2 whatever the frame was), observed verbatim:

        E   AssertionError: [(12.0, 120, 20, 1, True), (0.001, 200, 30, 2, False)]
        E   assert [(0.001, 200, 30, 2, False)] == [(0.001, 120, 20, 1, False)]
    """
    calls = _capped_rig(sim_hub, monkeypatch, tmp_path)
    gain, offset, binning = readout
    if path == "solve_and_sync":
        call = sim_hub.solve_and_sync(12.0)
    elif path == "sync_rotator_to_sky":
        call = sim_hub.sync_rotator_to_sky(exposure_s=12.0)
    elif path == "rotate_to_pa":
        call = sim_hub.rotate_to_pa(90.0, exposure_s=12.0)
    else:
        from astrodeck.polar import native
        session = type("S", (), {"solve_settings": {
            "exposure_s": 12.0, "gain": gain, "offset": offset,
            "binning": binning, "filter": None}})()

        class _NoSolve:
            name = "failing"

            async def solve(self, p, **kw):
                return SolveResult(False, message="Not enough stars.")
        call = native._capture_and_solve(sim_hub, _NoSolve(), session)
    with pytest.raises(light.NoLightError) as e:
        await asyncio.wait_for(call, 30.0)
    assert str(e.value).startswith(f"{prefix}: {light.NO_LIGHT_WORDS}"), e
    assert [c for c in calls if not c[4]] == [
        (0.001, gain, offset, binning, False)], calls
    assert not sim_hub._capture_lock.locked()


# ============================================================ a cancel

async def test_a_cancel_during_the_self_shot_propagates(tmp_path, bus_lines):
    """A cancel landing while the self-shot exposes (the caller's task
    cancelled, as ``ResumeArm.stop_recovery`` cancels the ladder's step)
    reaches the camera, which aborts, and propagates: it is not a failed
    self-shot, so no verdict is given, nothing is kept and the guard is free.

    RED under mutant "the cancel swallowed" (the self-shot's ``except``
    catches ``BaseException``), observed verbatim:

        E   Failed: DID NOT RAISE <class 'asyncio.exceptions.CancelledError'>
    """
    cam = _Sensor(bias=251.0, fault="hangs")
    hub = _Hub(_elsewhere(tmp_path), cam)
    task = asyncio.ensure_future(_fail(hub, _frame(_dark(251.0))))
    # A wall-clock deadline (#610): 200 x sleep(0.01) is 2 s on Linux but
    # 3.1 s on Windows (sleep rounds up to the 15.6 ms timer there), so a
    # round count gives the two platforms different real patience.
    await wait_until(lambda: bool(cam.shots), timeout_s=5.0, interval_s=0.01)
    assert cam.shots, "premise: the self-shot started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cam.cancelled == 1
    assert _evidence(bus_lines) == [], bus_lines
    assert light._SELF_REFERENCES == {}
    assert not hub._capture_lock.locked()


def _plan():
    from astrodeck.sequence import SequencePlan
    from astrodeck.sequence.models import ExposureStep, Target
    return SequencePlan(name="capped", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=3)])])


async def test_a_resume_arm_stop_cancels_the_self_shot(sim_hub, monkeypatch,
                                                       tmp_path, bus_lines):
    """End to end on the recovery ladder: auto-resume's blind solve fails on
    a capped optic, the library holds nothing at its readout, and the
    operator stops the ladder (``ResumeArm.stop_recovery``, as Abort does)
    while the self-shot is exposing. The camera sees the cancel, the solve
    ends in it rather than in a verdict, and the ladder stands down.

    RED under mutant "the cancel swallowed", observed verbatim:

        E   AssertionError: ['FailedSolveError']
        E   assert ['FailedSolveError'] == ['CancelledError']
    """
    import astrodeck.providers as providers
    from astrodeck.devices import fingerprint as _fp
    from astrodeck.sequence import SequenceEngine
    from astrodeck.sequence.resume_arm import ResumeArm
    from astrodeck.sequence.session import Session, session_store

    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    engine = SequenceEngine(sim_hub)

    async def limits(target, *, cfg=None, plan=None, projected=True):
        return None
    monkeypatch.setattr(engine, "check_slew_limits", limits)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda arm, s, t: True)
    monkeypatch.setattr(ResumeArm, "_can_solve", lambda arm: True)

    class _NoSolve:
        name = "failing"

        async def solve(self, path, **kw):
            return SolveResult(False, message="Not enough stars.")
    monkeypatch.setattr(providers, "pick_solver", lambda h: _NoSolve())
    sim_hub.master_library = _elsewhere(tmp_path)

    started = asyncio.Event()
    seen = {"cancelled": 0}
    cam = sim_hub.require("camera")

    async def expose(seconds, gain, offset, binning=1, light=True, **kw):
        if not light:
            started.set()
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                seen["cancelled"] += 1
                raise
        return _frame(_dark(251.0), seconds=seconds, gain=gain,
                      offset=offset, binning=binning)
    monkeypatch.setattr(cam, "expose", expose)

    ended: list[str] = []
    real = sim_hub.solve_and_sync

    async def spy(*a, **kw):
        try:
            return await real(*a, **kw)
        except BaseException as e:
            ended.append(type(e).__name__)
            raise
    monkeypatch.setattr(sim_hub, "solve_and_sync", spy)

    s = Session(name="capped", status="dormant", plan=_plan(),
                auto_resume=True)
    session_store.save(s)
    try:
        arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
        tick = asyncio.ensure_future(arm.tick())
        await asyncio.wait_for(started.wait(), 10.0)
        assert arm.recovering, "premise: the ladder is running"
        assert arm.stop_recovery("the operator stopped it",
                                 disarm=True) == s.id
        await asyncio.wait_for(tick, 10.0)
        assert ended == ["CancelledError"], ended
        assert seen["cancelled"] == 1
        assert _evidence(bus_lines) == [], bus_lines
        assert not arm.recovering
        assert not sim_hub._capture_lock.locked()
        stood = [m for lv, m, src in bus_lines if "stood down" in m]
        assert stood == ["auto-resume stood down for 'capped': the operator "
                         "stopped it"], bus_lines
    finally:
        try:
            session_store.delete(s.id)
        except Exception:                      # noqa: BLE001 - housekeeping
            pass
