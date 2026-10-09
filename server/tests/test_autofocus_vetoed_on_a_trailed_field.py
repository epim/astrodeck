# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#855: autofocus ran during a guider runaway and stored the slope it measured.

NGC 7331, night of 2026-10-07, F+17. A guider runaway inflated the HFR (4.29
against a 3.25 baseline), the relative-HFR watchdog fired a sweep, and the
sweep counted 529 'stars' on the trails, moved the focuser 11175 -> 11153 and
stored "this focuser defocuses at 0.0909 px/step" against the 0.078-0.080 that
sweeps on round stars measured that night.

What this file pins:

* a sweep is NOT STARTED while the guider is guiding over the guide-RMS
  ceiling, and the veto changes no state; a guider that is not guiding vetoes
  nothing; a plan that turned frame rejection off still gets the veto at the
  default ceiling;
* a runaway that starts MID-SWEEP (over 1.25 x the ceiling) abandons the sweep
  through its own teardown: focuser back, nothing stored, the sparse-field
  debt owed again, the last good focus kept;
* both sweep engines ask once more AFTER the last exposure, before anything is
  stored;
* a vetoed fire keeps its rule armed for free (a `once` rule and a cooldown
  included), while repeated mid-sweep abandons count as failures after the
  first, so a runaway watchdog sweeps at most four times;
* the band between the start line and the abort line rides out a wobble.

The engine code under test is real throughout. The guider is a real `Guider`
subclass; `run_autofocus` is replaced only where named, by a stand-in that
accepts the real keyword signature and drives the REAL `assert_tracking` with
the engine's REAL probe. Each test's docstring names the production mutant
that turns it RED and the assertion text observed under it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from _simhub import a_real_site

import astrodeck.focus.autofocus as AF
import astrodeck.focus.native as NATIVE
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as eng_mod
from astrodeck.config import load_focus_calibration
from astrodeck.devices.sim import build_sim_rig
from astrodeck.events import bus
from astrodeck.focus.autofocus import (AutofocusResult, FieldTrailing,
                                       assert_tracking, calibration_key,
                                       run_autofocus)
from astrodeck.guide.base import GuideStats, Guider
from astrodeck.hub import Hub
from astrodeck.providers import NATIVE_AVAILABLE
from astrodeck.sequence import Instruction, SequenceEngine, SequencePlan
from astrodeck.sequence.engine import (MAX_REARM_AFTER_FAILURE,
                                       SPARSE_RESWEEP_EVERY_S,
                                       TRAIL_VETO_LOG_EVERY_S)
from astrodeck.sequence.models import ExposureStep, Target

native_only = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="native wheel absent: the sequence autofocus sweep is the native engine",
)

HEALTHY = 1.45          # this rig's measured healthy guide RMS, arcsec


class _Guider(Guider):
    """A real ``Guider`` subclass, so the hub's teardown and any isinstance
    check meet the contract, not a duck."""
    name = "stub"

    def __init__(self, **stats):
        self.s = GuideStats(**stats)
        self.connected = True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def start_guiding(self):
        pass

    async def stop_guiding(self):
        pass

    async def dither(self, pixels: float = 3.0, settle=None):
        pass

    def stats(self):
        return self.s

    async def is_active(self):
        return self.s.guiding


async def _install(hub, stub):
    """Disconnect the guider the hub already has (the sim one), then put the
    stub in its place. The hub's own teardown disconnects whatever is
    installed, which is now the stub."""
    old = hub.guider
    if old is not None:
        await old.disconnect()
    hub.guider = stub


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """The `test_flip_before_the_limit.py` sim hub: its own config store under
    tmp_path, the legacy sim guider, a real site."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    a_real_site(monkeypatch)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _tracking(sim_hub, monkeypatch, value=True):
    """The mount says it is tracking (or not), so the probe's tracking half
    answers without the sim's own state deciding the test."""
    async def get_tracking():
        return value
    monkeypatch.setattr(sim_hub.devices["telescope"], "get_tracking",
                        get_tracking)


async def _engine(sim_hub, monkeypatch, stub, **plan_kw) -> SequenceEngine:
    await _install(sim_hub, stub)
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False, **plan_kw)
    eng._cfg = None
    return eng


def _recorder(calls, pos=10000):
    async def fake(*a, **k):
        calls.append(k)
        return AutofocusResult(True, pos, 3.0, [], "ok")
    return fake


def _flipping_sweep(stub, to_rms, calls):
    """A sweep stand-in that asks the engine's REAL probe through the REAL
    `assert_tracking` twice, with the guide RMS jumping to ``to_rms`` between
    the two asks: a runaway that starts mid-sweep."""
    async def fake(*a, **k):
        calls.append(k)
        await assert_tracking(k["tracking_check"], "p1")
        stub.s.rms_total = to_rms
        try:
            await assert_tracking(k["tracking_check"], "p2")
        finally:
            stub.s.rms_total = HEALTHY          # the next entry check sees calm
        return AutofocusResult(True, 10000, 3.0, [], "ok")
    return fake


def _said(q) -> list[tuple[str, str]]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "log":
            out.append((str(ev.data.get("level", "")),
                        str(ev.data.get("message", ""))))
    return out


async def _af(eng, label="triggered refocus"):
    q = bus.subscribe()
    try:
        ok = await eng._autofocus(label)
        return ok, _said(q)
    finally:
        bus.unsubscribe(q)


# ------------------------------------------------------- the entry veto

async def test_a_sweep_is_not_started_while_the_guider_trails(sim_hub,
                                                              monkeypatch):
    """THE F+17 SWEEP, REFUSED. Guiding at 773" is a trail; nothing is swept,
    nothing moves, and the log says why in one line.

    Mutant M11 "no entry veto" (the E9b ``if needs_tracking: ...`` block
    deleted), observed: ``AssertionError: a sweep ran on a trailing field: ok
    True, 1 sweep(s)``."""
    stub = _Guider(guiding=True, rms_total=773.0, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    foc = sim_hub.devices["focuser"]
    start = await foc.get_position()
    ok, said = await _af(eng)
    assert ok is None and not calls, (
        f"a sweep ran on a trailing field: ok {ok}, {len(calls)} sweep(s)")
    assert await foc.get_position() == start
    want = ('triggered refocus skipped: guide RMS 773.0" is over the 5.0" '
            'ceiling; a sweep would measure star trails')
    assert [m for lv, m in said if lv == "warning" and m == want] == [want], said


async def test_a_stopped_guider_vetoes_nothing(sim_hub, monkeypatch):
    """A stale RMS from a guider that is not guiding says nothing about the
    stars now; an unguided 2-6 s focus frame drifts under 2".

    Mutant M12 "veto ignores guiding" (``if not self._guiding_now(): return
    None`` deleted in `_field_trailing_now`), observed: ``AssertionError: an
    idle guider's stale 773" vetoed the sweep: ok None``."""
    stub = _Guider(guiding=False, rms_total=773.0, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ok, _ = await _af(eng)
    assert ok is True and len(calls) == 1, (
        f"an idle guider's stale 773\" vetoed the sweep: ok {ok}")


async def test_healthy_guiding_sweeps(sim_hub, monkeypatch):
    """Mutant M13 "inverted" (``rms <= limit`` -> ``rms > limit`` in
    `_field_trailing_now`), observed: ``AssertionError: healthy guiding at
    1.45" did not sweep: ok None``."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ok, _ = await _af(eng)
    assert ok is True and len(calls) == 1, (
        f'healthy guiding at {HEALTHY}" did not sweep: ok {ok}')


@pytest.mark.parametrize("rms, want", [(773.0, None), (HEALTHY, True)],
                         ids=["runaway", "healthy"])
async def test_the_veto_holds_when_frame_rejection_is_off(sim_hub, monkeypatch,
                                                          rms, want):
    """A 0 turns off FRAME rejection; a sweep on trails moves the focuser and
    stores a slope that outlives the night, so the veto falls back to the
    default ceiling.

    Mutant M14 "0 disarms the veto" (``ceiling = self._policy.max_guide_rms``
    plus ``if ceiling <= 0: return None``), observed on the runaway case:
    ``AssertionError: max_guide_rms=0, guide RMS 773.0: want None, got
    True``. Mutant M14b (``ceiling = self._policy.max_guide_rms`` alone),
    observed on the healthy case: ``AssertionError: max_guide_rms=0, guide
    RMS 1.45: want True, got None``."""
    stub = _Guider(guiding=True, rms_total=rms, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub, max_guide_rms=0.0)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ok, _ = await _af(eng)
    assert ok is want, f"max_guide_rms=0, guide RMS {rms}: want {want}, got {ok}"


async def test_a_vetoed_sweep_leaves_the_sparse_debt_owed(sim_hub, monkeypatch):
    """The veto is asked before anything changes, so a sweep owed since two
    sparse-field failures is still owed.

    Mutant M15 "veto after the resets" (the E9b block moved below
    ``self._sparse_resweep_owed = False``), observed: ``AssertionError: a
    vetoed sweep paid off the sparse-field debt``."""
    stub = _Guider(guiding=True, rms_total=773.0, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    eng._sparse_resweep_owed = True
    ok, _ = await _af(eng)
    assert ok is None and not calls
    assert eng._sparse_resweep_owed is True, (
        "a vetoed sweep paid off the sparse-field debt")


async def test_repeated_vetoes_say_it_once_per_quiet_spell(sim_hub,
                                                           monkeypatch):
    """Fix round 1. A vetoed refocus is re-asked at every frame boundary, so
    the line is rate limited: two vetoes inside TRAIL_VETO_LOG_EVERY_S say it
    once, and a veto after a quiet spell that long says it again.

    Mutant F1 "veto log return deleted" (``if last is not None and now - last <
    TRAIL_VETO_LOG_EVERY_S: return`` deleted from `_say_sweep_vetoed`), and
    mutant F2 "veto log not rate limited" (``TRAIL_VETO_LOG_EVERY_S = 0.0``),
    observed: ``AssertionError: two vetoes inside 600 s said 2 lines`` under
    F1 and ``... inside 0 s said 2 lines`` under F2."""
    stub = _Guider(guiding=True, rms_total=773.0, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    said_all: list = []
    for _ in range(2):
        ok, said = await _af(eng)
        assert ok is None
        said_all += said
    skipped = [m for lv, m in said_all if lv == "warning" and "skipped" in m]
    assert len(skipped) == 1, (
        f"two vetoes inside {TRAIL_VETO_LOG_EVERY_S:.0f} s said "
        f"{len(skipped)} lines")
    # A quiet spell one period long: the next veto is said again.
    eng._trail_veto_logged_at = time.monotonic() - TRAIL_VETO_LOG_EVERY_S - 1
    ok, said = await _af(eng)
    assert ok is None and not calls
    assert [m for lv, m in said if lv == "warning" and "skipped" in m], said


async def test_a_runaway_in_raw_pixels_vetoes_the_sweep(sim_hub, monkeypatch):
    """Fix round 1. A guider reporting raw pixels with no guide scale is judged
    through the floor (the smallest arcsec 480 px can be, 48.0"), so the sweep
    veto catches a runaway whether or not the focal length is set; the line
    says "at least" because the figure is a floor.

    Mutant F3 "veto ignores pixel floor" (``rms, exact =
    self._guide_rms(), True`` in `_field_trailing_now`), observed:
    ``AssertionError: a 480 px runaway with no guide scale did not veto the
    sweep: ok True``. Mutant F4 "at-least dropped from phrase", observed:
    ``AssertionError: [('warning', 'triggered refocus skipped: guide RMS
    48.0" is over ...')]``."""
    stub = _Guider(guiding=True, rms_total=480.0, is_arcsec=False,
                   image_scale=0.0)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ok, said = await _af(eng)
    assert ok is None and not calls, (
        f"a 480 px runaway with no guide scale did not veto the sweep: ok {ok}")
    want = ('triggered refocus skipped: guide RMS at least 48.0" is over the '
            '5.0" ceiling; a sweep would measure star trails')
    assert [m for lv, m in said if lv == "warning" and m == want] == [want], said


@pytest.mark.parametrize("kind", ["dark-step", "calibration-target"])
async def test_calibration_work_is_not_vetoed(sim_hub, monkeypatch, kind):
    """Fix round 1. Dark, bias and flat steps and calibration targets do not
    image the sky, so they are exempt from the trail veto exactly as from the
    tracking probe: the sweep runs, and with no probe wired.

    Mutant F5 "calibration vetoed too" (``if needs_tracking:`` -> ``if
    True:`` above the veto), observed: ``AssertionError: dark-step at a 773"
    guide RMS: want a sweep, got ok None and 0 sweep(s)``."""
    stub = _Guider(guiding=True, rms_total=773.0, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    if kind == "dark-step":
        kw = {"step": ExposureStep(filter="L", exposure_s=1.0, gain=100,
                                   count=1, frame_type="Dark")}
    else:
        kw = {"target": Target(name="cal", ra_hours=0.0, dec_deg=0.0,
                               calibration=True)}
    ok = await eng._autofocus("refocus", **kw)
    assert ok is True and len(calls) == 1, (
        f'{kind} at a 773" guide RMS: want a sweep, got ok {ok} and '
        f"{len(calls)} sweep(s)")
    assert calls[0].get("tracking_check") is None, calls[0]


# ------------------------------------------------- abandoned mid-sweep

@native_only
async def test_a_runaway_mid_sweep_abandons_and_stores_nothing(sim_hub,
                                                               monkeypatch):
    """THE PATH THE RIG RUNS: the real native sweep on the sim focuser and
    camera. The guide RMS jumps to 773" on the probe's 4th mount read; the
    sweep unwinds through its own teardown.

    Mutant M16 "probe swallowed" (`assert_tracking` without the ``except
    TrackingLost: raise`` arm), observed: ``AssertionError: a sweep through a
    runaway was not abandoned: ok True``. Mutant M17 "probe not wired" (E9d
    back to ``tracking_check=self._tracking_now``), observed: the same."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    await _install(sim_hub, stub)
    tel = sim_hub.devices["telescope"]
    foc = sim_hub.devices["focuser"]
    st = {"reads": 0}

    async def get_tracking():
        st["reads"] += 1
        if st["reads"] >= 4:
            stub.s.rms_total = 773.0
        return True

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    start = await foc.get_position()
    ok, said = await _af(eng)
    assert ok is None, f"a sweep through a runaway was not abandoned: ok {ok}"
    assert st["reads"] > 3, st
    assert await foc.get_position() == start, (
        f"left the focuser at {await foc.get_position()} instead of {start}")
    assert load_focus_calibration(calibration_key(foc)) is None, (
        "a slope measured on trails was stored")
    want = ('triggered refocus abandoned: guide RMS 773.0" is over 6.25", '
            '125% of the 5.0" ceiling; focuser back, nothing stored')
    assert any(m == want for lv, m in said if lv == "warning"), said


async def test_assert_tracking_passes_a_trailing_verdict_through():
    """The probe's own verdict reaches the sweep; any other exception is still
    "cannot say".

    Mutant M16, observed: ``Failed: DID NOT RAISE <class
    'astrodeck.focus.autofocus.FieldTrailing'>``."""
    async def trailing():
        raise FieldTrailing("x", limit=6.25)

    async def broken():
        raise RuntimeError("link down")

    with pytest.raises(FieldTrailing) as e:
        await assert_tracking(trailing, "p")
    assert e.value.limit == 6.25 and str(e.value) == "x"
    assert await assert_tracking(broken, "p") is None


async def test_the_mount_gate_still_words_a_stopped_mount(sim_hub, monkeypatch):
    """A stopped mount also blows up the guide RMS. Tracking is asked first, so
    the mount's own gate (and its recovery) owns that case.

    Mutant M19 "trail first" (the trail check moved above `_tracking_now` in
    `_sweep_probe`), observed: ``astrodeck.focus.autofocus.FieldTrailing:
    guide RMS 773.0" is over 6.25", 125% of the 5.0" ceiling``."""
    stub = _Guider(guiding=True, rms_total=773.0, is_arcsec=True)
    await _install(sim_hub, stub)
    _tracking(sim_hub, monkeypatch, False)
    monkeypatch.setattr(eng_mod, "TRACKING_CONFIRM_S", 0.0)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    assert await eng._sweep_probe() is False


async def test_a_first_mid_sweep_abandon_is_free_and_keeps_the_bookkeeping(
        sim_hub, monkeypatch):
    """Runs everywhere (no native wheel needed): the first abandon is not a
    failed refocus. The sparse debt is owed again, the last good focus and the
    cadence count are untouched, and the line says what happened.

    Mutant M22 "debt dropped" (the ``if owed_before:`` block deleted),
    observed: ``AssertionError: the abandoned sweep paid off the sparse-field
    debt``. Mutant M23 "focus forgotten" (``self._last_focus_at = None`` added
    to the arm), observed: ``AssertionError: assert None == 123.0``. Mutant
    M24 "arm missing" (the ``except FieldTrailing`` arm deleted), observed:
    ``AssertionError: a first abandon returned False``."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus",
                        _flipping_sweep(stub, 773.0, calls))
    eng._sparse_resweep_owed = True
    eng._sparse_resweep_next = None
    eng._last_focus_at = 123.0
    eng._frames_since_focus = 7
    t0 = time.monotonic()
    ok, said = await _af(eng)
    t1 = time.monotonic()
    assert ok is None, f"a first abandon returned {ok}"
    assert len(calls) == 1
    assert eng._sparse_resweep_owed is True, (
        "the abandoned sweep paid off the sparse-field debt")
    # Fix round 1: the re-owed debt is due one cadence on, not at once and
    # not never. Mutant F6 "sparse next not pushed" (the
    # ``self._sparse_resweep_next = ...`` line deleted from the arm),
    # observed: ``AssertionError: the re-owed debt is due at None``.
    nxt = eng._sparse_resweep_next
    assert nxt is not None and (t0 + SPARSE_RESWEEP_EVERY_S <= nxt
                                <= t1 + SPARSE_RESWEEP_EVERY_S), (
        f"the re-owed debt is due at {nxt}")
    assert eng._last_focus_at == 123.0
    assert eng._frames_since_focus == 7
    assert eng._trail_abandons == 1
    want = ('triggered refocus abandoned: guide RMS 773.0" is over 6.25", '
            '125% of the 5.0" ceiling; focuser back, nothing stored')
    lines = [m for lv, m in said if lv == "warning" and m == want]
    assert lines == [want], said
    assert len(want) <= 137


async def test_repeated_mid_sweep_abandons_count_as_failures_until_a_sweep_completes(
        sim_hub, monkeypatch):
    """BOUNDED: the second abandon in a row counts as a failed refocus; a sweep
    that runs to its end resets the run.

    Mutant M25 "no bound" (``if self._trail_abandons < MAX_TRAIL_ABANDONS:``
    -> ``if True:``), observed: ``AssertionError: a counted abandon did not
    restart the cadence`` (the second abandon returned None). Mutant M26
    "never reset" (the E9e ``self._trail_abandons = 0`` deleted), observed:
    ``assert (True is True and 3 == 0)``, ``where 3 = ..._trail_abandons``.

    Fix round 1, the temperature re-anchor (design 3.5: the exit for the
    temperature-refocus path). A free abandon leaves the baseline alone, so
    the temperature refocus is asked again once guiding recovers; a counted
    abandon re-anchors it, or a temperature refocus would stay due at every
    frame boundary. Mutant F7 "counted abandon no temp re-anchor" (``await
    self._capture_focus_temp()`` deleted from the counted arm), observed:
    ``AssertionError: a counted abandon left the temperature baseline at
    -99.0``. Mutant F8 "counted abandon skips frames reset" is the cadence
    assertion's: ``a counted abandon did not restart the cadence``."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    foc = sim_hub.devices["focuser"]

    async def get_temperature():
        return 7.5

    monkeypatch.setattr(foc, "get_temperature", get_temperature)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus",
                        _flipping_sweep(stub, 773.0, calls))
    got = []
    said_all: list = []
    for i in range(3):
        eng._frames_since_focus = 7
        eng._last_focus_temp = -99.0
        ok, said = await _af(eng)
        got.append(ok)
        said_all += said
        if i == 0:
            assert eng._last_focus_temp == -99.0, (
                "a free abandon re-anchored the temperature baseline")
        if i == 1:
            assert eng._frames_since_focus == 0, (
                "a counted abandon did not restart the cadence")
            assert eng._last_focus_temp == 7.5, (
                f"a counted abandon left the temperature baseline at "
                f"{eng._last_focus_temp}")
    assert got == [None, False, False], f"abandons returned {got}"
    counted = [m for lv, m in said_all if lv == "warning"
               and "abandoned 2 times running" in m]
    assert counted, said_all
    assert len(counted[0]) <= 137, (len(counted[0]), counted[0])

    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder([]))
    ok, _ = await _af(eng)
    assert ok is True and eng._trail_abandons == 0

    monkeypatch.setattr(eng_mod, "run_autofocus",
                        _flipping_sweep(stub, 773.0, calls))
    ok, _ = await _af(eng)
    assert ok is None, (
        f"after a completed sweep the next abandon returned {ok}")


async def test_hysteresis_rides_out_a_wobble_under_the_abort_line(sim_hub,
                                                                  monkeypatch):
    """Start at or under 5.0", abandon only over 6.25". A wobble to 5.5" mid-
    sweep is ridden out; 6.5" is abandoned; a START at 5.5" is refused.

    Mutant M27 "no band" (``TRAIL_SWEEP_ABORT_FACTOR = 1.0``), observed:
    ``AssertionError: a wobble to 5.5" mid-sweep: want True, got None``.
    Mutant M28 "band too wide" (``TRAIL_SWEEP_ABORT_FACTOR = 2.0``),
    observed: ``AssertionError: a runaway to 6.5" mid-sweep: want None, got
    True``. Mutant M29 "start uses the abort line" (E9b
    ``self._field_trailing_now()`` -> ``(aborting=True)``), observed:
    ``AssertionError: a sweep started at 5.5": ok True``."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    eng = await _engine(sim_hub, monkeypatch, stub)
    for to_rms, want in ((5.5, True), (6.5, None)):
        calls: list = []
        monkeypatch.setattr(eng_mod, "run_autofocus",
                            _flipping_sweep(stub, to_rms, calls))
        ok, said = await _af(eng)
        assert ok is want, (
            f'a {"wobble" if want else "runaway"} to {to_rms}" mid-sweep: '
            f"want {want}, got {ok}")
        if want:
            assert not [m for _lv, m in said if "abandoned" in m], said
        eng._trail_abandons = 0

    calls = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    stub.s.rms_total = 5.5
    ok, _ = await _af(eng)
    assert ok is None and not calls, f'a sweep started at 5.5": ok {ok}'


# --------------------------------------------- the trigger's bookkeeping

def _hfr_plan(rule: Instruction, frames: int = 12) -> SequencePlan:
    plan = SequencePlan(
        name="w", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=frames)])],
        instructions=[rule])
    return plan


def _soft_frames():
    async def _cap(*a, **k):
        return {"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}
    return _cap


async def _run(eng, plan, timeout=60.0):
    eng.start(plan)
    deadline = asyncio.get_event_loop().time() + timeout
    while eng.running:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"run did not finish: {eng.state}")
        await asyncio.sleep(0.02)


async def test_a_vetoed_triggered_refocus_keeps_its_rule_armed_for_free(
        sim_hub, monkeypatch):
    """A veto is not a failed attempt: the rule stays on its edge and fires at
    every soft frame until a sweep can run.

    Mutant M18 "None spends budget" (the E10 ``elif ok is None:`` arm deleted
    and ``if ok is False`` -> ``if not ok``), observed: ``AssertionError: a
    vetoed rule fired 3 times over 12 soft frames``. Mutant M18b "None counts
    as success" (the E10 arm deleted), observed: ``... fired 1 times ...``."""
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    calls: list = []

    async def af(label, **_ctx):
        calls.append(label)
        return None

    monkeypatch.setattr(eng, "_autofocus", af)
    monkeypatch.setattr(eng, "_capture", _soft_frames())
    await _run(eng, _hfr_plan(Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")))
    assert len(calls) == 12, (
        f"a vetoed rule fired {len(calls)} times over 12 soft frames")


async def test_a_runaway_watchdog_sweeps_at_most_four_times(sim_hub,
                                                            monkeypatch):
    """THE BOUND, END TO END: the real `_autofocus` under the real dispatcher,
    with every sweep abandoned mid-way. One free abandon, then
    MAX_REARM_AFTER_FAILURE counted ones, then the rule rests.

    Mutant M25 "no bound", observed: ``AssertionError: a runaway watchdog
    swept 12 times over 12 soft frames``."""
    stub = _Guider(guiding=True, rms_total=HEALTHY, is_arcsec=True)
    await _install(sim_hub, stub)
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus",
                        _flipping_sweep(stub, 773.0, calls))
    monkeypatch.setattr(eng, "_capture", _soft_frames())
    await _run(eng, _hfr_plan(Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")))
    assert len(calls) == 1 + MAX_REARM_AFTER_FAILURE == 4, (
        f"a runaway watchdog swept {len(calls)} times over 12 soft frames")


async def test_a_once_rule_gets_its_shot_back_after_a_veto(sim_hub,
                                                           monkeypatch):
    """A `once` rule whose one fire was vetoed has not had its one shot.

    Mutant M30 "once spent" (``rec.fired_count = max(0, rec.fired_count -
    1)`` deleted), observed: ``AssertionError: a once rule fired 1 times``."""
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    calls: list = []

    async def af(label, **_ctx):
        calls.append(label)
        return None if len(calls) <= 3 else True

    monkeypatch.setattr(eng, "_autofocus", af)
    monkeypatch.setattr(eng, "_capture", _soft_frames())
    await _run(eng, _hfr_plan(Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus", once=True)))
    assert len(calls) == 4, f"a once rule fired {len(calls)} times"


async def test_a_vetoed_fire_does_not_start_the_cooldown(sim_hub, monkeypatch):
    """A cooldown measures time since the rule's action RAN.

    Mutant M31 "cooldown started" (``rec.last_fire_ts = 0.0`` deleted),
    observed: ``AssertionError: a cooldown rule fired 1 times``."""
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)
    calls: list = []

    async def af(label, **_ctx):
        calls.append(label)
        return None if len(calls) == 1 else True

    monkeypatch.setattr(eng, "_autofocus", af)
    monkeypatch.setattr(eng, "_capture", _soft_frames())
    await _run(eng, _hfr_plan(Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus",
        cooldown_s=3600)))
    assert len(calls) == 2, f"a cooldown rule fired {len(calls)} times"


async def test_a_new_run_starts_with_no_abandons_and_a_fresh_veto_line(
        sim_hub, monkeypatch):
    """Fix round 1. The abandon run and the veto line's quiet spell belong to
    ONE run: a run that inherited an abandon from the last would count its
    first abandon as a failed refocus, and one that inherited the timestamp
    would swallow its first veto line. The run here takes no sweep, so only
    `start` can have cleared them.

    Mutant F9 "start keeps abandons" (``self._trail_abandons = 0`` deleted in
    `start`), observed: ``AssertionError: a new run inherited 1 abandon(s)``.
    Mutant F10 "start keeps the veto timestamp"
    (``self._trail_veto_logged_at = None`` deleted in `start`), observed:
    ``AssertionError: a new run inherited the veto line timestamp 5.0``."""
    _tracking(sim_hub, monkeypatch)
    eng = SequenceEngine(sim_hub)

    calls: list = []

    async def af(label, **_ctx):
        calls.append(label)
        return True

    monkeypatch.setattr(eng, "_autofocus", af)
    monkeypatch.setattr(eng, "_capture", _soft_frames())
    eng._trail_abandons = 1
    eng._trail_veto_logged_at = 5.0
    plan = _hfr_plan(Instruction(trigger="on_hfr_above", threshold=99.0,
                                 action="refocus"), frames=1)
    await _run(eng, plan)
    assert not calls, f"no sweep was expected: {calls}"
    assert eng._trail_abandons == 0, (
        f"a new run inherited {eng._trail_abandons} abandon(s)")
    assert eng._trail_veto_logged_at is None, (
        f"a new run inherited the veto line timestamp "
        f"{eng._trail_veto_logged_at}")


# ------------------------------------- one more ask before anything is stored

class _Counter:
    """Counts COMPLETED exposures (after the await returns, so a cancelled
    speculative frame does not count) and what the probe and the store saw."""

    def __init__(self):
        self.done = 0
        self.probe_saw: list[int] = []
        self.store_saw: list[int] = []
        self.last_probe_before_store: list[int] = []


def _wrap(monkeypatch, cam, module, c: _Counter):
    real_expose = cam.expose

    async def expose(*a, **k):
        r = await real_expose(*a, **k)
        c.done += 1
        return r

    monkeypatch.setattr(cam, "expose", expose)
    real_store = module.record_measured_span

    async def store(*a, **k):
        c.store_saw.append(c.done)
        c.last_probe_before_store.append(c.probe_saw[-1] if c.probe_saw
                                         else -1)
        return await real_store(*a, **k)

    monkeypatch.setattr(module, "record_measured_span", store)

    async def probe():
        c.probe_saw.append(c.done)
        return True
    return probe


@native_only
async def test_the_native_sweep_asks_once_more_before_storing(sim_hub,
                                                              monkeypatch):
    """The native probe runs before each MOVE, so without the last ask the
    last point's exposure and the confirming frame were never re-checked
    before the slope was written.

    Mutant M32 "no final native probe" (the line before the `done` arm's
    `record_measured_span` deleted), observed: ``AssertionError: the last
    probe saw 10 exposures, the store 11``."""
    cam = sim_hub.devices["camera"]
    foc = sim_hub.devices["focuser"]
    c = _Counter()
    probe = _wrap(monkeypatch, cam, NATIVE, c)
    q = bus.subscribe()
    try:
        r = await run_autofocus(cam, foc, hub=sim_hub, exposure_s=0.05,
                                gain=200, binning=2, tracking_check=probe)
        said = _said(q)
    finally:
        bus.unsubscribe(q)
    assert r.success, r.message
    assert len(c.store_saw) == 1, c.store_saw
    assert not [m for lv, m in said if lv == "info" and (
        "the engine refused this sweep" in m or "could not be measured" in m)
    ], "the sweep did not end in the `done` arm"
    assert c.last_probe_before_store[0] == c.store_saw[0], (
        f"the last probe saw {c.last_probe_before_store[0]} exposures, the "
        f"store {c.store_saw[0]}")


async def test_the_legacy_sweep_asks_once_more_before_storing(monkeypatch):
    """The legacy numpy sweep's per-point probe runs before each move too, so
    the last point and the validation frame were never re-checked.

    Mutant M33 "no final legacy probe" (the 4.3 item 3 line deleted),
    observed: ``AssertionError: the last probe saw 8 exposures, the store
    10``."""
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    c = _Counter()
    probe = _wrap(monkeypatch, cam, AF, c)
    r = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                            steps_each_side=4, binning=2, tracking_check=probe)
    assert r.success, r.message
    assert len(c.store_saw) == 1, c.store_saw
    assert c.last_probe_before_store[0] == c.store_saw[0], (
        f"the last probe saw {c.last_probe_before_store[0]} exposures, the "
        f"store {c.store_saw[0]}")
