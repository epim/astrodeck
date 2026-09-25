"""The session resets H1 left unguarded (#204, #210 follow-ups; H2, #189).

H1 added per-session host state to the native guider and reset it at the
session boundaries: ``start_guiding`` zeroes the #204 unconfirmed re-lock
count, the #219 dither widening of the re-lock radius and the #210 PPEC feed
time; ``stop_guiding`` clears the feed time once it is in the file;
``_note_lock``'s first lock spends a star-loss watch armed before it; and
``_relock_radius_px`` falls back to the engine's 15 px when the configured
search region is not a usable radius. No test failed when any one of those
lines was deleted. Each test below names its line and was shown RED with that
line deleted from guide/native.py, run from a byte-for-byte backup and
restored byte-identical afterwards; the observed failure is quoted verbatim.

Where another line hides a deletion's behaviour, the test says which line,
and asserts the state the session boundary promises instead: a test that
waits for behaviour that cannot change could not fail.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from types import SimpleNamespace

import pytest

import astrodeck.config as configmod
import astrodeck.guide.native as nativemod
from astrodeck.guide.native import (GP_DARK_VARIANCE,
                                    RELOCK_UNCONFIRMED_FRAMES, NativeGuider,
                                    _ENGINE_SEARCH_REGION_PX)
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.asyncio

needs_wheel = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                 reason="native wheel absent")


# ------------------------------------------------ a real start, on the reuse path


def _star_frame(cx, cy, w=64, h=64, amp=4000.0, sg=1.6, bg=100):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sg * sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)


_BLANK = _star_frame(32.0, 32.0, amp=0.0)

_IDENT_CAL = {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
              "y_angle": math.pi / 2, "y_angle_error": 0.0,
              "declination": 0.0, "pier_side": "west",
              "ra_parity": "even", "dec_parity": "even",
              "rotator_angle": 0.0, "binning": 1, "is_valid": True}


class _WallClock:
    """``guide.native``'s ``time`` module with a settable ``time()``;
    everything else delegates to the real module."""

    def __init__(self, wall: float = 50_000.0) -> None:
        self.wall = wall

    def time(self) -> float:
        return self.wall

    def __getattr__(self, name):
        return getattr(time, name)


class _ScriptCam:
    """Serves ``frames`` in order, one 5 s exposure of virtual clock each,
    then STARVES: the next exposure never returns until ``stop_guiding``
    cancels it, so a test reads the guider with its loop parked and nothing
    more processed. The first frame of a start is the reuse path's
    star-existence check; the loop gets the rest."""

    name = "fake guide camera"

    def __init__(self, clock: _WallClock, frames) -> None:
        self.clock = clock
        self.frames = list(frames)
        self.starved = asyncio.Event()
        self._never = asyncio.Event()

    async def expose(self, exposure_s, gain, offset, binning=1):
        if not self.frames:
            self.starved.set()
            await self._never.wait()
        self.clock.wall += 5.0
        return SimpleNamespace(data=self.frames.pop(0),
                               timestamp=self.clock.wall)


class _Mount:
    """Pier and declination matching ``_IDENT_CAL``, so the persisted
    calibration passes every reuse gate and the start takes the reuse path."""

    name = "fake mount"
    can_pulse_guide = True

    async def pulse_guide(self, direction, ms):
        return None

    async def guide_rates(self):
        return (0.004178, 0.004178)

    async def get_position(self):
        return (5.0, 0.0)

    async def pier_side(self):
        return SimpleNamespace(value="west")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A PPEC guider for profile ``prof1`` whose persisted calibration is
    reusable, on a virtual clock, and with NO persisted PPEC model, so no
    restore re-seeds the feed time. Returns ``(guider, clock)``."""
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    clock = _WallClock()
    monkeypatch.setattr(nativemod, "time", clock)
    d = tmp_path / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prof1.json").write_text(
        json.dumps({**_IDENT_CAL, "image_scale_arcsec": 2.0}), encoding="utf-8")
    g = NativeGuider(None, _Mount(),
                     config={"ra_algorithm": "ppec", "image_scale_arcsec": 2.0,
                             "image_scale_known": True, "exposure_s": 5.0},
                     profile_id="prof1")
    return g, clock


async def _start(g, clock, frames) -> None:
    """``start_guiding`` for real, then wait until the loop has processed
    every frame after the star check and is parked on the next exposure."""
    g.cam = _ScriptCam(clock, frames)
    await g.start_guiding()
    await asyncio.wait_for(g.cam.starved.wait(), timeout=30.0)


# ------------------------------------------------------------ start_guiding


@needs_wheel
async def test_a_start_clears_the_unconfirmed_relock_count(rig):
    """``start_guiding``: ``self._relock_unconfirmed = 0``.

    A session that ended counting frames with no star near the lock (#204)
    must not hand that count to the next. Left at ``N - 1``, the new
    session's first such frame would stop the guider.

    NO BEHAVIOUR SEES THIS LINE ALONE. The count is read only on a frame that
    has a lock baseline (``_lock_xy``), which this start clears, and the
    first lock that sets a new baseline zeroes the count itself
    (``_note_lock``'s first-lock branch). So this asserts what the start
    promises, read with the loop parked before its first frame.

    DELETED -- RED, observed verbatim:

        AssertionError: the new session starts 2 unconfirmed re-lock frames
        in, carried from the last one
    """
    g, clock = rig
    g._relock_unconfirmed = RELOCK_UNCONFIRMED_FRAMES - 1
    await _start(g, clock, [_star_frame(32.0, 32.0)])
    try:
        assert g._relock_unconfirmed == 0, (
            f"the new session starts {g._relock_unconfirmed} unconfirmed "
            f"re-lock frames in, carried from the last one")
    finally:
        await g.stop_guiding()


@needs_wheel
async def test_a_start_clears_the_dither_widening_of_the_relock_radius(rig):
    """``start_guiding``: ``self._lock_moved_px = 0.0``.

    The re-lock radius widens by however far dithers may have moved the lock
    since it was last measured (#219). A new session has measured nothing
    and dithered nothing, so its radius is the engine's search region. A
    carried 40 px would let a re-lock onto a different star up to 55 px away
    read as the lock star, and a new first lock does not re-measure it away.

    DELETED -- RED, observed verbatim:

        AssertionError: the new session's re-lock radius is 55.0 px, widened
        by the last session's dithers
    """
    g, clock = rig
    g._lock_moved_px = 40.0
    await _start(g, clock, [_star_frame(32.0, 32.0)])
    try:
        radius = g._relock_radius_px()
        assert radius == _ENGINE_SEARCH_REGION_PX, (
            f"the new session's re-lock radius is {radius} px, widened by the "
            f"last session's dithers")
    finally:
        await g.stop_guiding()


@needs_wheel
async def test_a_start_does_not_file_the_last_sessions_feed_time(rig, tmp_path):
    """``start_guiding``: ``self._gp_fed_at = None``.

    A loop that dies on its own (the reacquire budget, a camera fault) keeps
    its feed time, and a recovery restart comes straight back through
    ``start_guiding`` without a stop. The engine is rebuilt, so nothing has
    fed the new model. Here the new session locks and then loses the star:
    two dead-reckoned frames put two DARK points in the fresh PPEC window.

    UPDATED FOR #243, AND NOW NO FILE SEES THIS LINE. Until #243 the stop
    wrote that two-point window, and the file's stamp showed the carried
    feed time. Now the stop saves only a window with
    ``GP_MIN_MEASURED_POINTS`` measured points, and every one of them was
    measured by this session's loop, which stamps the feed time itself, or
    restored, which seeds it from the file. So this asserts what the start
    promises, read with the loop parked after the two dark frames, and that
    the stop wrote nothing.

    DELETED -- RED, observed verbatim:

        AssertionError: the new session carries the previous session's feed
        time (40000.0) and has measured nothing
    """
    g, clock = rig
    g._gp_fed_at = 40_000.0             # the previous session's last feed
    await _start(g, clock, [_star_frame(32.0, 32.0), _star_frame(32.0, 32.0),
                            _BLANK, _BLANK])
    try:
        window = g._engine.dump_gp_window()
        assert len(window) == 2 and all(
            r[2] == GP_DARK_VARIANCE for r in window), window
        assert g._gp_fed_at is None, (
            f"the new session carries the previous session's feed time "
            f"({g._gp_fed_at}) and has measured nothing")
    finally:
        await g.stop_guiding()
    # #243: a window of dark points only is not saved at all.
    assert not (tmp_path / "guider" / "prof1-gp.json").exists()


# ------------------------------------------------------------- stop_guiding


@needs_wheel
async def test_a_stop_leaves_no_feed_time_behind(rig, tmp_path):
    """``stop_guiding``: the final ``self._gp_fed_at = None``.

    Once the stop has written the session's feed time into the file the
    guider holds none, so nothing written after it can claim this session's
    feed as its own.

    NO BEHAVIOUR SEES THIS LINE ALONE today. ``_persist_gp_window`` has one
    caller, ``stop_guiding``, and it persists only for a stop that ended a
    live session; a stop after a stop is idle, and the next live session
    begins in ``start_guiding``, which clears the feed time itself. So this
    asserts the state the stop promises; a new caller of the persist would
    otherwise inherit the value this pins as gone.

    The session guides twelve measured frames after its lock, so its window
    clears #243's ``GP_MIN_MEASURED_POINTS`` and the stop really writes it
    (three frames were enough before #243).

    DELETED -- RED, observed verbatim:

        AssertionError: the stopped guider still holds the session's feed
        time (50070.0)
    """
    g, clock = rig
    star = _star_frame(32.0, 32.0)
    await _start(g, clock, [star] * 14)
    assert g._gp_fed_at == clock.wall, "the session fed nothing: no premise"
    await g.stop_guiding()
    assert (tmp_path / "guider" / "prof1-gp.json").exists()
    assert g._gp_fed_at is None, (
        f"the stopped guider still holds the session's feed time "
        f"({g._gp_fed_at})")


# ------------------------------------------------------ _note_lock, first lock


class _Frame:
    """``data`` is the frame INDEX; ``_FieldNative`` looks its stars up by it."""

    def __init__(self, index: int) -> None:
        self.data = index
        self.timestamp = time.time()


class _Cam:
    """Numbered frames; sets the stop flag on the last so the loop processes
    exactly the script."""

    name = "fake guide camera"

    def __init__(self, total: int, stop: asyncio.Event) -> None:
        self.total = total
        self.stop = stop
        self.n = 0

    async def expose(self, exposure_s, gain, offset, binning=1):
        if self.n >= self.total - 1:
            self.stop.set()
        index = self.n
        self.n += 1
        await asyncio.sleep(0)
        return _Frame(index)


class _Tel:
    name = "fake mount"

    async def pulse_guide(self, direction, ms):
        return None

    async def pier_side(self):
        raise NotImplementedError


class _ScriptEngine:
    """Plays back one Action per frame. ``stats()["guiding"]`` is True, as the
    real engine reports from the moment it is asked to guide, including on
    the re-acquire frames after a loss."""

    def __init__(self, actions) -> None:
        self.actions = list(actions)

    def process(self, data, ts, exposure_s):
        return self.actions.pop(0)

    def stats(self):
        return {"guiding": True, "settling": False, "recent": []}

    def dump_calibration(self):
        return None


class _FieldNative:
    """Stands in for the wheel: ``guide_star_find`` answers from a per-frame
    star field, brightest first."""

    def __init__(self, fields) -> None:
        self.fields = list(fields)

    def guide_star_find(self, data):
        return [{"x": float(x), "y": float(y), "snr": 40.0}
                for x, y in self.fields[int(data)]], {}


_LOST = {"action": "lock_lost", "reason": "star_lost"}
_IDLE = {"action": "idle"}
_PULSE = {"action": "pulse_pair", "ra": {"dir": "west", "ms": 100}, "dec": None}
_A = (400.0, 300.0)
_A_BACK = (400.4, 300.3)            # the same star, 0.5 px on


async def _run_script(monkeypatch, actions, fields):
    monkeypatch.setattr(nativemod, "_native", _FieldNative(fields))
    g = NativeGuider(None, _Tel(), config={"image_scale_arcsec": 5.5,
                                           "image_scale_known": True,
                                           "exposure_s": 0.01},
                     profile_id=None)
    g._engine = _ScriptEngine(actions)
    g.cam = _Cam(len(actions), g._stop)
    g._active = True
    g._stop.clear()
    await g._guide_loop()
    assert not g._engine.actions, "the loop did not process the script"
    return g


async def test_the_first_lock_spends_a_watch_armed_before_it(monkeypatch):
    """``_note_lock``, first-lock branch: ``self._relock_pending = False``.

    A star loss before the session's first lock arms the re-lock watch with
    no baseline to measure a re-lock against. The first lock IS the
    baseline, so it spends that watch: the frame after it is steady-state
    guiding, not a re-lock. Left armed, the next frame counts a phantom
    re-lock of the lock star against itself, and re-locks are what the
    re-lock hold and the "not guiding" judgement count.

    DELETED -- RED, observed verbatim:

        AssertionError: the first lock left the watch armed, and the next
        frame counted 1 phantom re-lock(s): [2.8]

    CONTROL (green deleted or not): the same frames with the loss AFTER the
    first lock count the one real re-lock, so this harness does reach the
    re-lock count.
    """
    g = await _run_script(monkeypatch,
                          actions=[_LOST, _IDLE, _PULSE],
                          fields=[[], [_A], [_A_BACK]])
    assert g._lock_xy is not None, "no first lock was taken: no premise"
    relocks = [e["arcsec"] for e in g._relock_events]
    assert g._relocks == 0 and not relocks, (
        f"the first lock left the watch armed, and the next frame counted "
        f"{g._relocks} phantom re-lock(s): {[round(a, 1) for a in relocks]}")
    # The baseline stays where the first lock put it, and the watch is spent.
    assert g._lock_xy == _A and not g._relock_pending


async def test_control_a_loss_after_the_first_lock_is_a_relock(monkeypatch):
    """CONTROL for the test above: lock, lose the star, find it again 0.5 px
    on. One re-lock, measured against the first lock."""
    g = await _run_script(monkeypatch,
                          actions=[_IDLE, _LOST, _IDLE, _PULSE],
                          fields=[[_A], [], [_A_BACK], [_A_BACK]])
    assert g._relocks == 1
    assert g._relock_events[0]["arcsec"] == pytest.approx(0.5 * 5.5, abs=0.05)


# ---------------------------------------------------------- _relock_radius_px


_MOVED_PX = 7.5


def _radius_for(**cfg) -> float:
    g = NativeGuider(None, _Tel(), config=cfg, profile_id=None)
    g._lock_moved_px = _MOVED_PX
    return g._relock_radius_px()


@pytest.mark.parametrize("region", [0.0, -3.0, math.nan, math.inf],
                         ids=["zero", "negative", "nan", "inf"])
async def test_an_unusable_search_region_falls_back_to_15_px(region):
    """``_relock_radius_px``: ``if not (r > 0 and math.isfinite(r)): r =
    _ENGINE_SEARCH_REGION_PX``.

    A search region of 0 or less, NaN or infinity is not a radius. The
    fallback is the engine's own 15 px, still widened by the moved distance
    (7.5 px here). A zero or negative radius finds no star near the lock
    ever, so the #204 guard would stop healthy guiding after every loss; an
    infinite one takes any star in the frame as the lock star; NaN compares
    false with everything.

    MUTANT "drop the positive/finite check" (the two lines deleted) -- RED on
    all four, observed verbatim:

        [zero]     AssertionError: search_region 0.0 gave a re-lock radius of
                   7.5 px, not the 15 px fallback plus 7.5 px moved
        [negative] AssertionError: search_region -3.0 gave a re-lock radius
                   of 4.5 px, not the 15 px fallback plus 7.5 px moved
        [nan]      AssertionError: search_region nan gave a re-lock radius of
                   nan px, not the 15 px fallback plus 7.5 px moved
        [inf]      AssertionError: search_region inf gave a re-lock radius of
                   inf px, not the 15 px fallback plus 7.5 px moved

    Each half on its own: "drop ``r > 0``" turns zero and negative RED (NaN
    and inf still fail ``isfinite``); "drop ``math.isfinite(r)``" turns inf
    RED (``nan > 0`` is already false).
    """
    radius = _radius_for(search_region=region)
    assert radius == _ENGINE_SEARCH_REGION_PX + _MOVED_PX, (
        f"search_region {region} gave a re-lock radius of {radius} px, not "
        f"the 15 px fallback plus {_MOVED_PX} px moved")


@pytest.mark.parametrize("cfg, expected",
                         [({"search_region": 20.0}, 20.0 + _MOVED_PX),
                          ({}, 15.0 + _MOVED_PX)],
                         ids=["usable_region_kept", "no_region_default"])
async def test_control_a_usable_search_region_is_kept(cfg, expected):
    """CONTROL, green under every mutant above: a real radius is used as
    configured, and an absent one is the engine default, each plus the moved
    distance."""
    assert _radius_for(**cfg) == expected
