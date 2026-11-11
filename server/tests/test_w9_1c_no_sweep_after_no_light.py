# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 9.1, part C (#1077): no autofocus sweep right after the light check has
said there is no light, and none under a cloud hold.

THE NIGHTS IT COMES FROM.

  * 2026-10-17 02:58: the solver said "still no light after holding" (median
    255 ADU against a 237 ADU reference) and the setup went on at the starting
    focus, into an autofocus sweep: 0 to 2 stars at every point, the slope
    blamed for it. Nothing the engine asked knew that the light check had
    spoken seconds before.
  * 2026-10-19 03:22: the meridian flip ran inside a cloud hold (held for
    cloud from 03:08:43) and its post-flip autofocus ran under a sky the
    engine had already called cloudy: 3 stars at the start, then 9, failed.

WHAT IT DOES NOW. `solve.light` keeps its last failed-solve verdict in process
memory. `_autofocus` and `_autofocus_for_hop` do not start a sweep within
`light.NO_LIGHT_FRESH_S` of a no-light verdict: they say why, carry on at the
focus the run has, and owe the probe-gated re-sweep. `_post_flip_focus_is_owed`
answers False while the run is holding for cloud and owes the same re-sweep,
due at the first light frame banked after the hold.

NO CLOCK, NO SLEEP. The verdict memory reads `light._clock`, replaced here by a
list-driven fake, so the bound is tested at 299.9 s and at 300.0 s without
waiting. Nothing here reads the wall clock, a site or the Sun.

STATE. The memory is module state. The autouse fixture resets it before and
after every test, so no test inherits another's sky.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as eng_mod
from _simhub import a_real_site
from astrodeck.solve.base import SolveResult
from astrodeck.events import bus
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import (SPARSE_RESWEEP_EVERY_S,
                                       TRAIL_VETO_LOG_EVERY_S)
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.solve import light


class _Clock:
    """The verdict memory's clock, set by the test."""
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean_verdict_memory(monkeypatch):
    light.reset_last_verdict()
    clock = _Clock()
    monkeypatch.setattr(light, "_clock", clock)
    yield clock
    light.reset_last_verdict()


@pytest.fixture
def clock(_clean_verdict_memory) -> _Clock:
    return _clean_verdict_memory


class _Rig:
    """A hub stand-in for the unit cases: all `failed_solve_error` reads of it
    is its calibration library."""
    master_library = object()


@pytest.fixture
def rig() -> _Rig:
    return _Rig()


async def _fail_with(monkeypatch, kind: str, hub, *,
                     narrowband: str | None = None):
    """One failed solve of ``hub``'s whose light check says ``kind``, through
    the real `failed_solve_error` (the call every solve path makes)."""
    monkeypatch.setattr(
        light, "_look",
        lambda frame, library, tol: (light.LightVerdict(kind), None, ""))
    frame = type("F", (), {"exposure_s": 12.0, "binning": 2, "gain": 200,
                           "offset": 30, "temperature_c": 5.0,
                           "data": None})()
    return await light.failed_solve_error(
        frame, SolveResult(False, message="Not enough stars."),
        prefix="plate solve failed", hub=hub, narrowband_filter=narrowband)


# ------------------------------------------------- the verdict memory itself

def test_the_bound_is_five_minutes():
    assert light.NO_LIGHT_FRESH_S == 300.0


async def test_a_no_light_verdict_stands_up_to_the_bound_and_not_past_it(
        monkeypatch, clock, rig):
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    assert light.no_light_just_now(rig) == light.NO_LIGHT_WORDS
    clock.now += light.NO_LIGHT_FRESH_S - 0.1
    assert light.no_light_just_now(rig) == light.NO_LIGHT_WORDS, (
        "299.9 s is fresh")
    clock.now += 0.1
    assert light.no_light_just_now(rig) is None, "300.0 s is not"
    assert light.no_light_just_now(rig, max_age_s=600.0) == light.NO_LIGHT_WORDS


async def test_nothing_remembered_is_no_verdict(rig):
    assert light.no_light_just_now(rig) is None


async def test_a_verdict_is_the_hubs_that_judged_the_frame(monkeypatch, rig):
    """The memory is about one rig's camera: another hub (a test's, a profile
    swap's) is not vetoed by it, and it keeps nobody alive."""
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    assert light.no_light_just_now(_Rig()) is None
    assert light.no_light_just_now(rig) is not None


async def test_a_hub_that_cannot_be_weakly_referenced_leaves_no_verdict(
        monkeypatch):
    class Slotted:
        __slots__ = ("master_library",)

        def __init__(self):
            self.master_library = object()
    hub = Slotted()
    await _fail_with(monkeypatch, light.NO_LIGHT, hub)
    assert light.no_light_just_now(hub) is None


@pytest.mark.parametrize("kind, narrowband", [
    (light.CLOUD, None), (light.UNKNOWN, None), (light.NO_LIGHT, "S")],
    ids=["cloud", "unknown", "narrowband"])
async def test_only_a_no_light_verdict_is_no_light(monkeypatch, rig, kind,
                                                   narrowband):
    """A cloud, an unjudged frame and a frame at the no-light level through a
    narrowband filter (an open optic) are not a dark sky."""
    await _fail_with(monkeypatch, kind, rig, narrowband=narrowband)
    assert light.no_light_just_now(rig) is None


async def test_a_later_verdict_replaces_an_earlier_one(monkeypatch, clock, rig):
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    clock.now += 10.0
    await _fail_with(monkeypatch, light.CLOUD, rig)
    assert light.no_light_just_now(rig) is None, (
        "a lit verdict clears a dark one")
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    assert light.no_light_just_now(rig) is not None


async def test_reset_forgets_the_verdict(monkeypatch, rig):
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    light.reset_last_verdict()
    assert light.no_light_just_now(rig) is None


async def test_the_words_carry_no_number(monkeypatch, rig):
    await _fail_with(monkeypatch, light.NO_LIGHT, rig)
    words = light.no_light_just_now(rig)
    assert words and not any(c.isdigit() for c in words), words


# ------------------------------------------------------------- the engine

@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """The sim hub of `test_autofocus_vetoed_on_a_trailed_field.py`: its own
    config store under tmp_path, a real (made-up) site."""
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


def _engine(sim_hub, monkeypatch) -> SequenceEngine:
    async def get_tracking():
        return True
    monkeypatch.setattr(sim_hub.devices["telescope"], "get_tracking",
                        get_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    return eng


def _recorder(calls):
    async def fake(*a, **k):
        calls.append(k)
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


async def _run(coro):
    q = bus.subscribe()
    try:
        return await coro, _said(q)
    finally:
        bus.unsubscribe(q)


TARGET = Target(name="NGC 1499", ra_hours=4.0, dec_deg=36.0)


async def test_a_sweep_is_not_started_right_after_a_no_light_verdict(
        sim_hub, monkeypatch):
    """THE MUTANT'S TEST. The light check has just said no light: nothing is
    swept, nothing moves, no bookkeeping changes, the debt is made, and the log
    says why in one line."""
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    await _fail_with(monkeypatch, light.NO_LIGHT, sim_hub)
    foc = sim_hub.devices["focuser"]
    start = await foc.get_position()
    eng._last_focus_at = 123.0
    eng._frames_since_focus = 7
    t0 = time.monotonic()

    ok, said = await _run(eng._autofocus("initial autofocus"))
    t1 = time.monotonic()

    assert ok is None and not calls, (
        f"a sweep ran right after a no-light verdict: ok {ok}, "
        f"{len(calls)} sweep(s)")
    assert await foc.get_position() == start
    assert eng._last_focus_at == 123.0 and eng._frames_since_focus == 7
    assert eng._sparse_resweep_owed is True and not eng._sparse_resweep_due
    assert (t0 + SPARSE_RESWEEP_EVERY_S <= eng._sparse_resweep_next
            <= t1 + SPARSE_RESWEEP_EVERY_S)
    lines = [m for lv, m in said if lv == "warning"
             and m.startswith("initial autofocus skipped")]
    assert len(lines) == 1, said
    assert light.NO_LIGHT_WORDS in lines[0], lines


async def test_the_veto_is_said_once_then_again_after_the_log_interval(
        sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder([]))
    await _fail_with(monkeypatch, light.NO_LIGHT, sim_hub)

    _, first = await _run(eng._autofocus("triggered refocus"))
    _, second = await _run(eng._autofocus("triggered refocus"))
    eng._no_light_veto_said_at = time.monotonic() - TRAIL_VETO_LOG_EVERY_S - 1
    _, third = await _run(eng._autofocus("triggered refocus"))

    def skipped(said):
        return [m for _lv, m in said if "skipped" in m]
    assert len(skipped(first)) == 1
    assert skipped(second) == [], "said again inside the interval"
    assert len(skipped(third)) == 1


async def test_a_vetoed_sweep_keeps_the_debt_a_due_resweep_was_paying(
        sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder([]))
    await _fail_with(monkeypatch, light.NO_LIGHT, sim_hub)
    eng._sparse_resweep_owed = True
    eng._sparse_resweep_due = True
    eng._sparse_resweep_next = 0.0

    await _run(eng._autofocus("autofocus", resweep=True))

    assert eng._sparse_resweep_owed is True
    assert eng._sparse_resweep_due is False
    assert eng._sparse_resweep_next > time.monotonic()


async def test_a_sweep_runs_once_the_verdict_has_aged_out(sim_hub, monkeypatch,
                                                          clock):
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    await _fail_with(monkeypatch, light.NO_LIGHT, sim_hub)
    clock.now += light.NO_LIGHT_FRESH_S

    ok, _ = await _run(eng._autofocus("triggered refocus"))

    assert ok is True and len(calls) == 1


@pytest.mark.parametrize("kind, narrowband", [
    (light.CLOUD, None), (light.UNKNOWN, None), (light.NO_LIGHT, "S")],
    ids=["cloud", "unknown", "narrowband"])
async def test_a_sweep_runs_after_a_verdict_that_is_not_no_light(
        sim_hub, monkeypatch, kind, narrowband):
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    await _fail_with(monkeypatch, kind, sim_hub, narrowband=narrowband)

    ok, _ = await _run(eng._autofocus("triggered refocus"))

    assert ok is True and len(calls) == 1


async def test_a_sweep_runs_when_nothing_has_failed(sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ok, _ = await _run(eng._autofocus("triggered refocus"))
    assert ok is True and len(calls) == 1
    assert eng._sparse_resweep_owed is False


async def test_the_hop_gives_its_first_acquisition_back(sim_hub, monkeypatch):
    """`_autofocus_for_hop` answers False, so `_setup_target` calls
    `_give_back_first_acquisition` and the next hop owes the sweep again; and
    it does not send the mount to another panel to focus."""
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    other = Target(name="NGC 1499 2-2", ra_hours=4.1, dec_deg=36.0)

    async def never(*a, **k):
        raise AssertionError("the mount was sent to another panel")
    monkeypatch.setattr(eng, "_centre_once", never)
    await _fail_with(monkeypatch, light.NO_LIGHT, sim_hub)

    owed, said = await _run(eng._autofocus_for_hop(TARGET, other, None))

    assert owed is False and not calls
    assert eng._sparse_resweep_owed is True
    assert any("skipped" in m for _lv, m in said), said
    key = eng._focus_group_key(TARGET)
    eng._focus_groups_acquired.add(key)
    eng._give_back_first_acquisition(TARGET, True)
    assert key not in eng._focus_groups_acquired


async def test_the_hop_runs_its_sweep_when_the_sky_said_nothing(
        sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    ran, _ = await _run(eng._autofocus_for_hop(TARGET, TARGET, None))
    assert ran is True and len(calls) == 1


# ----------------------------------------------- the post-flip sweep, in a hold

async def test_the_post_flip_sweep_waits_out_a_cloud_hold(sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    eng._holding_for_clear = True
    eng._last_focus_at = None       # after a failure: owed, today, always
    t0 = time.monotonic()

    owed, said = await _run(eng._post_flip_focus_is_owed())
    t1 = time.monotonic()

    assert owed is False
    assert eng._sparse_resweep_owed is True and not eng._sparse_resweep_due
    assert t0 <= eng._sparse_resweep_next <= t1, (
        "the re-sweep is due at the first light frame after the hold")
    lines = [m for lv, m in said if lv == "warning"
             and "post-flip autofocus" in m]
    assert len(lines) == 1 and "holding for cloud" in lines[0], said


async def test_the_deferred_post_flip_sweep_falls_due_at_the_first_light_frame(
        sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    eng._holding_for_clear = True
    await _run(eng._post_flip_focus_is_owed())
    eng._holding_for_clear = False
    light_step = ExposureStep(filter="L", exposure_s=1.0, gain=100, count=1)

    eng._note_sparse_resweep(light_step, TARGET)

    assert eng._sparse_resweep_due is True


async def test_the_post_flip_sweep_is_owed_as_before_outside_a_hold(
        sim_hub, monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    eng._holding_for_clear = False
    eng._last_focus_at = None
    owed, _ = await _run(eng._post_flip_focus_is_owed())
    assert owed is True
    assert eng._sparse_resweep_owed is False


async def test_a_fresh_focus_is_still_skipped_outside_a_hold(sim_hub,
                                                             monkeypatch):
    eng = _engine(sim_hub, monkeypatch)
    eng._last_focus_at = time.monotonic()
    owed, _ = await _run(eng._post_flip_focus_is_owed())
    assert owed is False
    assert eng._sparse_resweep_owed is False, (
        "a recent good focus owes nothing; only the hold owes a sweep")


async def test_another_hubs_no_light_verdict_vetoes_nothing_here(sim_hub,
                                                                 monkeypatch):
    """The verdict is one hub's. A different hub's dark sky (an earlier test's,
    another profile's) must not keep this engine from a sweep it is owed."""
    eng = _engine(sim_hub, monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng_mod, "run_autofocus", _recorder(calls))
    other = _Rig()
    await _fail_with(monkeypatch, light.NO_LIGHT, other)

    ok, _ = await _run(eng._autofocus("triggered refocus"))

    assert ok is True and len(calls) == 1
