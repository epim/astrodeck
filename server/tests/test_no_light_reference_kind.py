"""A no-light verdict says what its reference stands on (#308, S3
orchestrator ruling 8).

WHY IT MATTERS. ``astrodeck.solve.light`` judges a failed solve's frame
against the median the camera reads with no light on it, and that level
comes from one of four places: the dark library's master for the frame's own
settings, a bias master plus the least dark current the doubling law allows,
a frame the check shot itself at the camera's shortest exposure standing in
for that bias (#262), or a reference a caller handed in. Only the first IS
the frame's no-light level. The others are floors drawn from a pedestal, and
#308 records the question none of them has answered on the rig: whether a
thick overcast over a dark site with no Moon can sit inside the band of a
cold sensor's pedestal. ResumeArm backs off to its hourly retry only on a
verdict a dark master stands behind, so it must be able to tell them apart
by something other than the evidence line's words.

So ``Reference.kind`` names the source as data (``DARK_MASTER``,
``BIAS_MASTER``, ``SELF_SHOT`` or ``EXPLICIT``, the default for a reference
handed in), ``reference_for`` sets it on every branch, and a failed solve's
exception exposes it as ``reference_kind``. These cases hold each branch of
``reference_for``, the end-to-end path through ``failed_solve_error`` for the
three a rig can reach, a kind nobody defined, and the repr the recorded
failures elsewhere quote.

Each case names the mutation that turns it red and the failure it produced,
verbatim, from a run of that mutant in a private scratch copy of server/
(issue #254).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.calibration.matcher import MasterRecord
from astrodeck.devices.base import CameraFrame
from astrodeck.hub import Hub
from astrodeck.solve import light
from astrodeck.solve.base import SolveResult

#: Big enough that a median is a median, small enough to be quick.
SHAPE = (240, 320)

#: A fixed night, so no case inherits another's kept self-reference.
NIGHT = "2026-09-25"


@pytest.fixture(autouse=True)
def _a_fresh_night(monkeypatch):
    light._SELF_REFERENCES.clear()
    monkeypatch.setattr(light, "night_key", lambda ts=None: NIGHT)
    yield
    light._SELF_REFERENCES.clear()


def _dark(level: float, seed: int = 308) -> np.ndarray:
    """A frame with no light on it: a Gaussian core at ``level``, sigma 9,
    the #251 frame's shape without its warm pixels."""
    rng = np.random.default_rng(seed)
    return np.clip(np.rint(rng.normal(level, 9.0, SHAPE)), 0,
                   65535).astype(np.uint16)


def _frame(data: np.ndarray) -> CameraFrame:
    """The #251 solve frame's settings: 12 s, gain 200, offset 30, bin 2,
    18.5 C."""
    return CameraFrame(data=data, exposure_s=12.0, gain=200, offset=30,
                       binning=2, bayer_pattern=None, temperature_c=18.5,
                       timestamp=0.0, data_is_linear=True)


def _record(kind: str, *, gain: int = 200, path: str = "") -> MasterRecord:
    return MasterRecord(id=f"{kind.lower()}-g{gain}", frame_type=kind,
                        exposure_s=0.0 if kind == "BIAS" else 12.0,
                        gain=gain, offset=30, temp_c=18.5, binning=2,
                        filter="", frame_count=20, path=path, built_ts=1.0)


def _master(tmp_path: Path, kind: str, level: float, *,
            gain: int = 200) -> MasterRecord:
    """A master on disk at the frame's readout (or ``gain``), float32 pixels
    around ``level``, as ``CalibrationLibrary.build`` writes one."""
    path = tmp_path / f"{kind.lower()}_{level:g}_g{gain}.fits"
    rng = np.random.default_rng(7)
    fits.PrimaryHDU((level + rng.normal(0, 0.5, (64, 64)))
                    .astype(np.float32)).writeto(path)
    return _record(kind, gain=gain, path=str(path))


class _Library:
    def __init__(self, masters):
        self._masters = masters

    def list_masters(self):
        return list(self._masters)


class _Cam:
    """A capped, shutterless camera: every exposure reads ``level``."""
    name = "kind camera"
    connected = True

    def __init__(self, level: float):
        self.level = level
        self.shots = 0

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target=""):
        self.shots += 1
        return CameraFrame(data=_dark(self.level, seed=self.shots),
                           exposure_s=seconds, gain=gain, offset=offset,
                           binning=binning, bayer_pattern=None,
                           temperature_c=18.5, timestamp=0.0,
                           data_is_linear=True)


class _Hub:
    """What ``failed_solve_error`` reads of a hub, with the REAL guard."""
    exposure_guard = Hub.exposure_guard

    def __init__(self, library, cam):
        self.master_library = library
        self.devices = {"camera": cam}
        self._capture_lock = asyncio.Lock()
        self._capture_busy = None


async def _fail(hub) -> light.FailedSolveError:
    return await light.failed_solve_error(
        _frame(_dark(251.0)), SolveResult(False, message="Not enough stars."),
        prefix="plate solve failed", hub=hub)


# ============================================================ reference_for

def test_each_branch_of_reference_for_names_its_kind():
    """``reference_for`` on a table of masters, one branch at a time: the
    matched dark master, a bias master at the readout, the self-shot handed
    in when the library has neither, and a ``Reference`` built by hand.

    RED under mutant "a bias is a dark master" (the bias branch files
    ``kind=DARK_MASTER``), observed verbatim:

        E   AssertionError: {'bias': 'dark_master', 'dark': 'dark_master', 'explicit': 'explicit', 'self': 'self_shot'}
        E   assert {'bias': 'dar...: 'self_shot'} == {'bias': 'bia...: 'self_shot'}
        E     Omitting 3 identical items, use -vv to show
        E     Differing items:
        E     {'bias': 'dark_master'} != {'bias': 'bias_master'}
        E     Use -v to get more diff

    RED under mutant "the self-shot is a bias master" (the self-reference
    branch files ``kind=BIAS_MASTER``, the kind of the master it stands in
    for), observed verbatim:

        E   AssertionError: {'bias': 'bias_master', 'dark': 'dark_master', 'explicit': 'explicit', 'self': 'bias_master'}
        E   assert {'bias': 'bia...'bias_master'} == {'bias': 'bia...: 'self_shot'}
        E     Omitting 3 identical items, use -vv to show
        E     Differing items:
        E     {'self': 'bias_master'} != {'self': 'self_shot'}
        E     Use -v to get more diff
    """
    frame = _frame(_dark(251.0))
    levels = {"dark-g200": 251.0, "bias-g200": 240.0, "bias-g150": 240.0}

    def level_of(m):
        return levels.get(m.id)

    dark, _ = light.reference_for(frame, [_record("DARK")], level_of=level_of)
    bias, _ = light.reference_for(frame, [_record("BIAS")], level_of=level_of)
    selfshot, _ = light.reference_for(
        frame, [_record("BIAS", gain=150)], level_of=level_of,
        self_bias=light.SelfBias(level=240.0, detail="self-reference"))
    kinds = {"dark": dark.kind, "bias": bias.kind, "self": selfshot.kind,
             "explicit": light.Reference(level=250.0).kind}
    assert kinds == {"dark": light.DARK_MASTER, "bias": light.BIAS_MASTER,
                     "self": light.SELF_SHOT,
                     "explicit": light.EXPLICIT}, kinds


def test_a_kind_nobody_defined_is_refused():
    """A kind outside the four is a typo, and a typo would read as "not a
    dark master" silently: it is refused when the reference is made.

    RED under mutant "any kind accepted" (``Reference.__post_init__``'s check
    removed), observed verbatim:

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="dark"):
        light.Reference(level=250.0, kind="dark")
    assert set(light.REFERENCE_KINDS) == {light.DARK_MASTER, light.BIAS_MASTER,
                                          light.SELF_SHOT, light.EXPLICIT}


def test_the_kind_stays_out_of_the_repr():
    """Control: ``LightVerdict.evidence`` prints a reference's words, and the
    recorded failures elsewhere in the suite quote a reference's repr from
    before this field existed, as they do for ``ceiling``. The kind is data
    for code, so it is left out of the repr, and a hand-made reference
    reads as it always did.

    RED under mutant "the kind in the repr" (``repr=False`` dropped from the
    field), observed verbatim:

        E   assert "Reference(le...d='explicit')" == "Reference(le...', detail='')"
        E     Skipping 65 identical leading characters in diff, use -v to show
        E     -  detail='')
        E     +  detail='', kind='explicit')
    """
    assert repr(light.Reference(level=250.0)) == (
        "Reference(level=250.0, sigma=1.0, source='an explicit reference', "
        "detail='')")


# ====================================================== failed_solve_error

async def test_the_failed_solve_error_carries_the_kind(tmp_path):
    """End to end, the three kinds a rig can reach, each a capped frame at
    251 ADU judged no light: against the dark master for its settings,
    against a bias master at its readout, and, with nothing at its readout,
    against the frame the check shoots itself; then once more against that
    self-shot kept from earlier tonight. ``reference_kind`` is the kind of
    the reference the verdict stood on.

    RED under mutant "the error does not look" (``reference_kind`` answers
    None whatever the verdict), observed verbatim:

        E   AssertionError: {'bias': None, 'dark': None, 'kept': None, 'self': None}
        E   assert {'bias': None... 'self': None} == {'bias': 'bia...: 'self_shot'}
        E     Differing items:
        E     {'bias': None} != {'bias': 'bias_master'}
        E     {'self': None} != {'self': 'self_shot'}
        E     {'kept': None} != {'kept': 'self_shot'}
        E     {'dark': None} != {'dark': 'dark_master'}
        E     Use -v to get more diff
    """
    cam = _Cam(251.0)
    errors = {
        "dark": await _fail(_Hub(_Library([_master(tmp_path, "DARK", 251.0)]),
                                 cam)),
        "bias": await _fail(_Hub(_Library([_master(tmp_path, "BIAS", 251.0)]),
                                 cam)),
    }
    elsewhere = _Library([_master(tmp_path, "BIAS", 240.0, gain=150)])
    errors["self"] = await _fail(_Hub(elsewhere, cam))
    errors["kept"] = await _fail(_Hub(elsewhere, cam))
    assert all(isinstance(e, light.NoLightError) for e in errors.values()), (
        errors)
    assert cam.shots == 1, "premise: the second self-shot verdict was kept"
    kinds = {k: e.reference_kind for k, e in errors.items()}
    assert kinds == {"dark": light.DARK_MASTER, "bias": light.BIAS_MASTER,
                     "self": light.SELF_SHOT, "kept": light.SELF_SHOT}, kinds


async def test_a_failure_with_no_reference_has_no_kind(tmp_path):
    """Control: no library loaded, so no reference and no verdict. The
    error is a plain ``FailedSolveError`` whose ``reference_kind`` is None,
    never a kind by default.

    RED under mutant "no reference reads as explicit" (``reference_kind``
    answers ``EXPLICIT`` when there is no reference), observed verbatim:

        E   AssertionError: assert 'explicit' is None
        E    +  where 'explicit' = FailedSolveError('plate solve failed: Not enough stars. (no level check was possible: no calibration library is loaded)').reference_kind
    """
    e = await _fail(_Hub(None, _Cam(251.0)))
    assert not isinstance(e, light.NoLightError), e
    assert e.verdict.reference is None
    assert e.reference_kind is None
