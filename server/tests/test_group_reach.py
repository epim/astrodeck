# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Reachability at selection: a panel the mount cannot reach now WAITS, and one
it can never reach tonight is REFUSED (#189 S2, task T18; spec 5.1
selection item 1, 6.2, D9; #132).

ONE PREDICATE, TWO ASKERS. `_mount_floor_verdict` is the slew gate as an
answer: the altitude floor, the horizon and obstruction mask, the no-go
wedges and the zenith keep-out come back tagged ``wait`` (time changes
them), and no saved site while limits are set, or a pier-side change with
meridian flips off, come back tagged ``refuse`` (waiting cannot fix either).
`_enforce_mount_floor` raises on exactly the verdicts it returns.

THE HOT LOOP THIS REPLACES. Two of the three earlier designs asked the gate
after the scheduler had chosen the panel, and told a scheduler that still
thought the panel ready that it was blocked: the scheduler chose it again
at once, with no sleep in between. Deciding it in the selection makes the
panel a WAITER with a wake time, so the existing do-while wait runs, with
the safety gate and the idle watch armed.

The runs are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py), with ``coords_clock`` wherever the simulator
mount's pier side is read. The fixture site is 40 N 74 W, not anybody's
rig, and no altitude or azimuth is printed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim. Every mutant was applied in a private scratch copy of
server/, never in the shared tree (#254).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, LAT, LON, T0, Night, grid_plan,
                            group_hub, group_store, ra_at)
from astrodeck.catalog.coords import altaz
from astrodeck.config import SafetyConfig, Site
from astrodeck.sequence import schedule
from astrodeck.sequence.engine import SlewRefused
from astrodeck.sequence.models import SequencePlan, Target
from astrodeck.sequence.session import session_store


def _unsave_site(store) -> None:
    """No saved site: the default the config starts with (latitude 0,
    longitude 0, ``is_default``). `ConfigStore.set_site` marks whatever it
    saves as configured, so the default is put back directly."""
    store.cfg().site = Site()


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 before_run=None, **kw) -> Night:
    night = Night(hub, monkeypatch, coords_clock=True, **kw)
    if before_run is not None:
        before_run(night)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


# ------------------------------------------------------- one predicate

def _target(ha_h: float, dec: float = 40.0) -> Target:
    return Target(id="t", name="Probe", ra_hours=ra_at(ha_h), dec_deg=dec)


async def test_the_gate_raises_exactly_when_the_verdict_is_not_none(
        group_hub, group_store, monkeypatch):
    """A table of limits and destinations. For every row, `_enforce_mount_
    floor` raises exactly when `_mount_floor_verdict` returns a verdict, the
    raise is that verdict (its sentence, its words, its numbers), and the
    verdict carries the row's tag: ``wait`` for the floor, the mask, a
    wedge and the keep-out, which the passing hours change; ``refuse`` for
    no saved site while limits are set, and for a pier-side change with
    flips off. The spec's "pier limit" under wait has no separate kind: the
    gate's only pier refusal is the flips-off change, and with flips on it
    lets a side change through to the meridian rule (row "pier-flips-on"),
    which waits for the crossing.

    MUTANT "two predicates" (`_mount_floor_verdict` no longer asks the pier
    guard, while `_enforce_mount_floor` keeps its own copy of it): RED
    (observed):
        AssertionError: ('pier-flips-off', None, SlewRefused('slew to Probe
        would require a pier flip but meridian flip is disabled'))
        assert (None is None) == (SlewRefused('slew to Probe would require a
        pier flip but meridian flip is disabled') is None)
    MUTANT "no site waits" (``REACH_TAGS["no_site"] = "wait"``): RED
    (observed):
        AssertionError: {'floor': ('wait', 'floor'), 'inside-the-limits': None,
        'keep-out': ('wait', 'ceiling'), 'mask': ('wait', 'floor'), ...}
          Differing items:
          {'no-site': ('wait', 'no_site')} != {'no-site': ('refuse', 'no_site')}
    """
    night = Night(group_hub, monkeypatch, coords_clock=True)
    eng = night.engine
    tel = group_hub.devices["telescope"]
    tel.rig.ra_hours, tel.rig.dec_deg = ra_at(-2.0), 40.0   # the west side
    high = _target(-2.0)
    alt, az = altaz(high.ra_hours, high.dec_deg, LAT, LON, T0)
    assert 30.0 < alt < 80.0, "premise: a target well inside the sky"
    past = _target(1.0)
    flips_on = SequencePlan(targets=[high], meridian_flip=True)
    flips_off = SequencePlan(targets=[high], meridian_flip=False)
    rows = {
        "nothing-configured": (SafetyConfig(), high, flips_on, None),
        "inside-the-limits": (SafetyConfig(min_alt_deg=alt - 20.0), high,
                              flips_on, None),
        "floor": (SafetyConfig(min_alt_deg=alt + 5.0), high, flips_on,
                  ("wait", "floor")),
        "mask": (SafetyConfig(horizon=[(0.0, alt + 5.0), (360.0, alt + 5.0)]),
                 high, flips_on, ("wait", "floor")),
        "wedge": (SafetyConfig(nogo_box=[{"az_min": az - 10.0,
                                          "az_max": az + 10.0,
                                          "alt_max": alt + 5.0}]),
                  high, flips_on, ("wait", "floor")),
        "keep-out": (SafetyConfig(max_alt_deg=alt - 5.0), high, flips_on,
                     ("wait", "ceiling")),
        "pier-flips-off": (SafetyConfig(enforce_pier_limits=True), past,
                           flips_off, ("refuse", "pier")),
        "pier-flips-on": (SafetyConfig(enforce_pier_limits=True), past,
                          flips_on, None),
        "no-site": (SafetyConfig(min_alt_deg=10.0), high, flips_on,
                    ("refuse", "no_site")),
    }
    try:
        got = {}
        for name, (cfg, target, plan, want) in rows.items():
            if name == "no-site":
                _unsave_site(group_store)
            whole = SimpleNamespace(safety=cfg)
            verdict = await eng._mount_floor_verdict(target, True, cfg=whole,
                                                     plan=plan)
            raised = None
            try:
                await eng._enforce_mount_floor(projected=True, target=target,
                                               cfg=whole, plan=plan)
            except SlewRefused as e:
                raised = e
            assert (verdict is None) == (raised is None), (name, verdict, raised)
            if verdict is not None:
                assert str(raised) == verdict.sentence, name
                assert raised.words == verdict.words, name
                assert raised.site_detail == verdict.site_detail, name
            got[name] = None if verdict is None else (verdict.tag, verdict.kind)
        assert got == {name: row[3] for name, row in rows.items()}, got
    finally:
        await night.close()


# ------------------------------------------------ a panel behind the mask

async def test_a_panel_behind_the_mask_waits_without_spinning(
        group_hub, group_store, monkeypatch):
    """A one-panel mosaic behind a horizon mask it rises clear of 15 min
    into the night. The panel is a WAITER: the scheduler asks again every
    ``REACH_RECHECK_S`` through `_wait_until`, whose safety gate runs on
    every 5 s tick, and it is first slewed to at the minute it clears. The
    number of selections (each asks the gating once for the panel) stays
    at one or two per simulated minute, and the published wait says why in
    words.

    The selection counter trips a guard at 50 selections inside one
    simulated minute, which raises inside the engine: a scheduler that
    never yields would otherwise hang the test, since the fake clock only
    moves when every engine task is parked.

    MUTANT "return waiting after selection" (A's original: `_eligibility_
    now` asks no reachability, and `_visit_panel` asks the gate first and
    returns "requeued" on a wait verdict): RED (observed):
        AssertionError: selections per simulated minute: [(0, 51)]
        assert 51 <= 3
    """
    ra, dec = ra_at(-2.0), 40.0
    clear_at = T0 + 900.0
    mask, _az = altaz(ra, dec, LAT, LON, clear_at)
    alt0, _az0 = altaz(ra, dec, LAT, LON, T0)
    assert alt0 < mask, "premise: the panel starts behind the mask"
    group_store.set_safety(SafetyConfig(enabled=False,
                                        horizon=[(0.0, mask), (360.0, mask)]))
    per_minute: dict[int, int] = {}
    real = schedule.gating_status

    def gating_status(target, site, twilight, now, **kw):
        if target.name == _name("1-1"):
            minute = int((now - T0) // 60)
            per_minute[minute] = per_minute.get(minute, 0) + 1
            if per_minute[minute] > 50:
                raise RuntimeError("the scheduler is spinning on a blocked "
                                   "panel")
        return real(target, site, twilight, now, **kw)

    monkeypatch.setattr(schedule, "gating_status", gating_status)
    plan = grid_plan(rows=1, cols=1, panel_kw={"count": 2})
    night = await _night(group_hub, monkeypatch, plan)
    waited = {m: n for m, n in per_minute.items() if m < 15}
    assert max(per_minute.values()) <= 3, (
        f"selections per simulated minute: {sorted(per_minute.items())[:6]}")
    assert night.done, night.trace[-3:]
    assert len(waited) == 15, sorted(waited)
    first = night.gotos[0][0]
    assert clear_at <= first <= clear_at + 60.0, (first - T0)
    gated = [t for t, ctx in night.gates if t < clear_at and ctx == "frame"]
    assert len(gated) >= 15 * 60 / engine_mod.SCHEDULE_WAIT_STEP_S - 12, (
        len(gated))
    waits = [s for s in night.states
             if (s.get("schedule") or {}).get("state") == "waiting"]
    assert waits and waits[0]["detail"] == f"waiting for {_name('1-1')}"
    reason = waits[0]["schedule"]["reason"]
    assert "altitude floor" in reason and "°" not in reason, reason
    assert night.stored.status == "complete"


# --------------------------------------------------- what waiting can't fix

async def test_a_pier_change_with_flips_off_sets_that_panel_aside(
        group_hub, group_store, monkeypatch):
    """Flips off, the pier guard armed, and a 1x2 whose 1-2 stands half an
    hour past the meridian while the mount tracks on the side for 1-1, east
    of it. 1-2 would need a pier change the plan does not allow: REFUSED, so
    it is set aside tonight with the gate's own sentence, recorded for the
    night and never slewed to, while 1-1, reachable from the side the mount
    is on, shoots every frame. The night ends; the session stays owed.

    MUTANT "every verdict waits" (``REACH_TAGS`` mapping every kind to
    "wait"): RED (observed, RE-PINNED IN S4-SIM):
        AssertionError: the run was still going at the fake horizon:
        [[10680.0, 'state', {... 'detail': 'waiting for M31 1-2', ...
        'schedule': {'state': 'waiting', 'reason': 'a slew to M31 1-2 would
        need a pier flip, and this plan has meridian flips switched off',
        'eta_s': 0}, ...}], ...]
        assert False
    Before S4-SIM the same mutant failed on the shots instead: 1-2 waited,
    and was shot once the simulator's side, which then followed where it
    pointed, came round with 1-1 past the meridian, a pier change with flips
    off (``Left contains 6 more items, first extra item: 'M31 1-2'``). The
    simulator mount now keeps the side its goto chose (#298), so the panel
    waits for a side change that never comes, which is the waiting the
    refusal exists to cut short.
    """
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=True))

    def setup(night):
        tel = group_hub.devices["telescope"]
        tel.rig.ra_hours, tel.rig.dec_deg = ra_at(-2.0), 40.0

    plan = grid_plan(rows=1, cols=2, panel_kw={"ha_h": -2.0, "ha_step_h": 2.5},
                     meridian_flip=False)
    night = await _night(group_hub, monkeypatch, plan, before_run=setup,
                         wall_s=30.0, horizon_s=3 * 3600.0)
    assert night.done, (
        f"the run was still going at the fake horizon: {night.trace[-2:]}")
    assert [t for t, _f in night.shots()] == [_name("1-1")] * 6
    assert all(who != _name("1-2") for _t, who in night.gotos), night.gotos
    sentence = (f"slew to {_name('1-2')} would require a pier flip but "
                f"meridian flip is disabled")
    assert [(r["target_id"], r["reason"]) for r in night.stored.set_aside] == [
        ("p01", sentence)], night.stored.set_aside
    assert night.said(f"M31: {sentence}; set aside for tonight")
    assert night.stored.status == "dormant", night.stored.status


async def test_no_saved_site_with_limits_set_ends_the_run(
        group_hub, group_store, monkeypatch):
    """A floor is configured and no site is saved: every altitude would be
    computed for latitude 0, longitude 0, so no panel can be checked, and
    waiting will not save a site. The selection raises the slew gate's own
    `SlewRefused`, a SafetyAbort, so the run ends unsafe before any slew,
    as the gate at the first hop always ended it.

    MUTANT "every verdict waits": RED (observed):
        AssertionError: the run was still going at the fake horizon:
        [[10680.0, 'state', {... 'detail': 'waiting for M31 1-1', ...
        'schedule': {'state': 'waiting', 'reason': "no observing site is saved,
        so no slew can be checked against the mount's limits", 'eta_s': 0},
        ...}], ...]
        assert False
    """
    group_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=10.0))
    _unsave_site(group_store)
    plan = grid_plan(rows=1, cols=2)
    night = await _night(group_hub, monkeypatch, plan, wall_s=30.0,
                         horizon_s=3 * 3600.0)
    assert night.done, (
        f"the run was still going at the fake horizon: {night.trace[-2:]}")
    last = night.states[-1]
    assert last["state"] == "aborted" and last["end_reason"] == "unsafe", last
    assert "no observing site is saved" in last["detail"], last["detail"]
    assert night.gotos == [] and night.captures == []


# ---------------------------------------------------- the harness's hygiene

def test_no_module_keeps_the_group_store_after_the_block(tmp_path):
    """The group harness's config store stands in for the app's only inside
    `config_store_as`. A module imported for the first time inside it, which
    binds ``config_store`` at import as the AM5 driver does, is handed back
    the store that was there before; a module bound to the real store, and
    one with no store at all, are left as they were. Without it, the first
    group test in a process left the AM5 driver reading the fixture site for
    every test after it, and test_pier_side_is_published's AM5 predictions
    failed three ways (seen running test_group_rotation.py before it,
    ``-p no:randomly -n0``, on the engine before this task).

    MUTANT "no sweep" (`config_store_as`'s ``finally`` putting nothing
    back): RED (observed):
        AssertionError: a module imported inside the block kept the group store
        assert <astrodeck.config.ConfigStore object at 0x000001F0BE87BE60> is
        <astrodeck.config.ConfigStore object at 0x000001F0BDCBB200>
    """
    import sys
    import types

    import astrodeck.config as config_mod
    from _group_harness import config_store_as
    from astrodeck.config import ConfigStore

    real = config_mod.config_store
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    names = ("astrodeck._t18_imported_inside", "astrodeck._t18_bound_real",
             "astrodeck._t18_no_store")
    try:
        with pytest.MonkeyPatch.context() as mp:
            with config_store_as(store, mp):
                assert config_mod.config_store is store
                inside, bound, plain = (types.ModuleType(n) for n in names)
                inside.config_store = config_mod.config_store  # bound now
                bound.config_store = real
                plain.other = store
                for mod in (inside, bound, plain):
                    sys.modules[mod.__name__] = mod
        assert config_mod.config_store is real
        assert sys.modules[names[0]].config_store is real, (
            "a module imported inside the block kept the group store")
        assert sys.modules[names[1]].config_store is real
        assert sys.modules[names[2]].other is store
        assert not hasattr(sys.modules[names[2]], "config_store")
    finally:
        for n in names:
            sys.modules.pop(n, None)
