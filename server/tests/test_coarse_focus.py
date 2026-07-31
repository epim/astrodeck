"""Coarse focus — the way out of the loop that cost a night.

Autofocus needs stars to start; a badly-defocused rig has none. This shrinks
the defocus BLOB until autofocus can take over, learning the focuser's real
travel from moves that fail along the way.

The blob physics are simulated (r = k*|x - focus|) rather than mocked to a
constant, so the search has to actually converge instead of being told it did.
"""
import asyncio

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.focus import coarse
from astrodeck.focus.coarse import run_coarse_focus
from astrodeck.imaging.defocus import HANDOVER_R80_PX, BlobSize


class _Focuser:
    """A focuser with a REAL travel that may be narrower than it advertises —
    exactly the rig's situation, where max_position reads 600000 and the usable
    range was 360 steps."""

    def __init__(self, max_position=60000, start=0, real_lo=0, real_hi=60000):
        self.max_position = max_position
        self._pos = start
        self.real_lo, self.real_hi = real_lo, real_hi
        self.visited: list[int] = []

    async def get_position(self):
        return self._pos

    async def move_to(self, p):
        p = int(p)
        reachable = max(self.real_lo, min(self.real_hi, p))
        self._pos = reachable
        self.visited.append(reachable)
        if reachable != p:
            raise DeviceError(
                f"move to {p} did not happen — stopped at {reachable}")


class _Frame:
    def __init__(self, pos):
        self.data = pos          # carries the position; the fake measurer reads it


class _Camera:
    def __init__(self, focuser):
        self.focuser = focuser
        self.exposures = 0

    async def expose(self, *a, **k):
        self.exposures += 1
        return _Frame(self.focuser._pos)


def _patch_blob(monkeypatch, focus=38000.0, k=0.02, blind=False):
    """measure_blob() reads the position out of the fake frame and returns the
    size the physics says it should be."""
    def fake(data, **kw):
        if blind:
            return None
        r = abs(float(data) - focus) * k
        return BlobSize(r80=r, peak=5000.0, snr=100.0, x=10, y=10,
                        background=600.0, sigma=20.0)
    monkeypatch.setattr(coarse, "measure_blob", fake)


def _run(monkeypatch, *, focus=38000.0, k=0.02, blind=False, **fkw):
    foc = _Focuser(**fkw)
    cam = _Camera(foc)
    _patch_blob(monkeypatch, focus, k, blind)
    res = asyncio.run(run_coarse_focus(cam, foc, exposure_s=0.0))
    return res, foc, cam


# ----------------------------------------------------------- convergence

@pytest.mark.parametrize("start,focus", [
    (0, 38000.0), (59000, 12000.0), (30000, 30000.0), (5000, 55000.0),
])
def test_it_finds_focus_from_anywhere(monkeypatch, start, focus):
    res, _, _ = _run(monkeypatch, focus=focus, start=start)
    assert res.success is True, res.message
    assert abs(res.best_position - focus) * 0.02 <= HANDOVER_R80_PX


def test_it_hands_over_rather_than_polishing(monkeypatch):
    """The V-curve autofocus measures HFR far better than this can; every extra
    probe here is a wasted exposure."""
    res, _, cam = _run(monkeypatch, focus=38000.0, start=0)
    assert res.success
    assert "autofocus" in res.message.lower()
    assert cam.exposures <= 9, f"took {cam.exposures} exposures"


def test_already_in_focus_costs_one_exposure(monkeypatch):
    res, foc, cam = _run(monkeypatch, focus=30000.0, start=30000)
    assert res.success and cam.exposures == 1
    assert foc.visited == []


# ------------------------------------------- learning the real travel

def test_it_narrows_the_range_when_a_move_is_refused(monkeypatch):
    """The rig's actual failure: max_position says 60000, the focuser physically
    stops at 20000. The search must learn that rather than retrying forever."""
    res, foc, _ = _run(monkeypatch, focus=50000.0, start=0,
                       max_position=60000, real_lo=0, real_hi=20000)
    assert res.success is False
    assert all(p <= 20000 for p in foc.visited), foc.visited


def test_a_focuser_with_almost_no_travel_says_so_clearly(monkeypatch):
    """360 usable steps out of a claimed 600000 — tonight's rig. The message has
    to name the cause, because the user has to go and physically fix it."""
    res, _, _ = _run(monkeypatch, focus=200000.0, start=0,
                     max_position=600000, real_lo=0, real_hi=360)
    assert res.success is False
    low = res.message.lower()
    assert "limit" in low or "jam" in low or "travel" in low, res.message


def test_it_never_claims_success_it_cannot_have(monkeypatch):
    res, _, _ = _run(monkeypatch, focus=500000.0, start=0,
                     max_position=600000, real_lo=0, real_hi=1000)
    assert res.success is False


# ------------------------------------------------------------ honesty

def test_an_unmeasurable_frame_is_reported_not_guessed(monkeypatch):
    """A fabricated size would send the search somewhere invented."""
    res, _, _ = _run(monkeypatch, blind=True, start=1000)
    assert res.success is False
    low = res.message.lower()
    assert "sky" in low or "cover" in low or "measur" in low, res.message


def test_cancel_returns_the_focuser_to_where_it_started(monkeypatch):
    """A stranded focuser means the rig keeps shooting from a random position."""
    foc = _Focuser(start=25000)
    cam = _Camera(foc)
    _patch_blob(monkeypatch, 38000.0, 0.02)

    async def _cancel():
        t = asyncio.ensure_future(run_coarse_focus(cam, foc, exposure_s=0.0))
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(_cancel())
    assert foc._pos == 25000


def test_a_focuser_reporting_no_travel_is_refused(monkeypatch):
    foc = _Focuser(max_position=0)
    cam = _Camera(foc)
    _patch_blob(monkeypatch)
    with pytest.raises(DeviceError, match="travel"):
        asyncio.run(run_coarse_focus(cam, foc, exposure_s=0.0))
