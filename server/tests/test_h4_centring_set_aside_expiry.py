"""A centring set-aside expires once per panel per night (#534, H4 orchestrator
ruling 2; spec 5.1, 3.4 ``Session.set_aside``, 5.9 same-night restart, 6.9).

THE NIGHT IT COMES FROM. The first mosaic on the rig (NGC 1499, a 3x2,
2026-09-29) started low in the east behind an obstruction, in moonlit haze.
Every panel failed centring, three strikes set each aside "for tonight: a
restart tonight does not retry it", and the night was lost to its first
hour, although the target transited three hours later.

WHAT IT DOES NOW. A set-aside made by a streak of centring failures and
nothing else is set aside FOR NOW: the panel stays in the rotation as a
waiter, and is tried once more 45 minutes after it was set aside, TIME ONLY
(``group_rules.set_aside_expiry``, engine ``_expire_or_wait``). Tried again
and struck out again, it is set aside for the rest of the night, and a
same-night restart does not expire it a second time (the session marks the
expired record, ``Session.note_set_aside_expired``). A floor or a reject
set-aside never expires. A crash-resume applies the same time-only rule
itself, needing no ephemeris (``resume_arm.standing_set_asides``).

AMENDED BY BACKLOG WP-07 (#564, 2026-09-30). The ruling as first built also
freed a panel early once its centre had risen 10 degrees since it was set
aside, a SITE-DERIVED altitude comparison (#534's original text, and this
file's own "the rise case" below, before this amendment); even narrowed by
a floor under how soon it could fire (a prior #564 fix), an early "rise"
answer still told a viewer the site sat within about 28 degrees of the
equator. WP-07 dropped that branch outright: ``set_aside_expiry`` now takes
no altitude at all, and every latitude behaves alike. The rise case this
file once ran at 5 N (`test_a_panel_that_has_climbed_10_degrees_is_tried_
before_45_minutes`) is replaced below by a case proving the site plays no
part at that same low latitude.

THE RUNS are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py): a 2x2 of 30 s frames, L and R three times each,
2 h east of the meridian at the fixture site (40 N 74 W, nobody's rig), and
2-2's centring scripted to fail; the WP-07 replacement case moves the
fixture to 5 N, where the pre-amendment rule would have freed the panel in
about 41 minutes, well inside the 45.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-A-r2-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted where
it was seen, verbatim (the first assertion lines, long lines wrapped).
"""
from __future__ import annotations

import re

import pytest

import astrodeck.catalog as catalog_mod
import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_ID, GROUP_NAME, LON, T0, Night,
                            grid_plan, group_hub, group_store, panel)
from astrodeck.catalog.coords import altaz
from astrodeck.config import Site
from astrodeck.events import night_key
from astrodeck.sequence.group_rules import (CENTRING, SET_ASIDE_EXPIRY_S,
                                            SET_ASIDE_RISE_DEG)
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.resume_arm import (nothing_to_shoot_tonight,
                                           recentre_candidates,
                                           standing_set_asides)
from astrodeck.sequence.session import Session, session_store

MISSES = f"{GROUP_NAME} 2-2"
#: The first three passes: 2-2 misses at 120, 180 and 360 s, and is set aside
#: at 540 s, when the pass its third miss was in closes (the other panels'
#: last frames end then). Measured on this harness; each case re-derives
#: ``set_at`` from the set-aside's own line rather than trusting this.
FIRST_THREE = [120.0, 180.0, 360.0]
#: A restart later the same night: after the first run's end, well before
#: the next night's key.
RESTART = T0 + 4500.0


@pytest.fixture(autouse=True)
def _one_night():
    """Premise, on the machine's own zone: the restart is the same observing
    night as the run it restarts."""
    assert night_key(T0) == night_key(RESTART)


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _misses(first: int | None = None, until: float | None = None):
    """2-2's centring fails: on its first ``first`` gotos of the Night, or
    while the night's clock is before ``until``, or always."""
    def goto(who, n, result):
        if who != MISSES:
            return result
        if first is not None and n > first:
            return result
        if until is not None and engine_mod.time.time() >= until:
            return result
        return {**result, "centered": False, "error_arcmin": None}
    return goto


async def _night(hub, monkeypatch, plan, *, t0: float = T0, session=None,
                 **kw) -> Night:
    night = Night(hub, monkeypatch, t0=t0, **kw)
    try:
        night.done = await night.run(
            plan, **({"session": session} if session is not None else {}))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _tried(night: Night, who: str = MISSES) -> list[float]:
    return [night.rel(t) for t, name in night.gotos if name == who]


def _set_aside_lines(night: Night) -> list[tuple[float, str]]:
    return [(night.rel(t), m) for t, lvl, m in night.lines
            if lvl == "warning" and "set aside for" in m and MISSES[4:] in m]


def _skips(night: Night) -> list[str]:
    return [e["reason"] for e in night.engine.reporter.build().safety_events
            if e.get("action") == "skip"]


def _numbers_beyond_the_words(line: str) -> list[str]:
    """The numbers a line about a set-aside says, other than a panel label
    ("2-2"), a count of the three-strike rule ("1 of 3 consecutive", "on 3
    consecutive visits"), the pass it closed, the panels it tried, and the
    ruling's own minutes: what is left would be a number the site decided.
    The mosaic's own name ("M31") is a name, and goes first."""
    s = line.replace(GROUP_NAME, "")
    s = re.sub(r"\b\d-\d\b", "", s)
    s = re.sub(r"\(\d of 3 consecutive\)|on 3 consecutive visits", "", s)
    s = re.sub(r"\b(45|10) minutes\b", "", s)
    s = re.sub(r"in pass \d+|of the \d+ panels", "", s)
    return re.findall(r"\d+(?:\.\d+)?", s)


def _assert_in_words(night: Night) -> None:
    """Every line the expiry and the set-aside say is in words: no altitude,
    no degree, no number beyond the words (6.9, H3 orchestrator ruling 1)."""
    lines = [m for _t, _lvl, m in night.lines
             if "set aside" in m or "set-aside" in m or "centring" in m]
    assert lines, "premise: the night said something about the set-aside"
    for m in lines:
        assert "°" not in m and " deg" not in m and "altitude" not in m, m
        assert _numbers_beyond_the_words(m) == [], m


# ------------------------------------------------------------- the time half

async def test_a_struck_out_panel_is_tried_once_more_45_minutes_later(
        group_hub, monkeypatch):
    """2-2 misses its first three passes and is set aside for now, at 540 s.
    It is tried once more exactly ``SET_ASIDE_EXPIRY_S`` later, although it
    has not risen 10 degrees (a premise computed here), centres, and is
    visited until it completes, its three rounds 60 s apart, as the only
    panel left; the mosaic completes. The record says what set it aside and
    when, and is marked expired; the set-aside's line says it will be tried
    again and never "a restart tonight does not retry it"; the expiry's line
    says why in words, and is not site-derived; no line carries an
    altitude; the report marks nothing skipped, since nothing was skipped.

    RED under mutant "set-aside never expires" (``_expire_or_wait`` asking
    no expiry: ``cause = None``), observed:

        AssertionError: 2-2 was tried at [120.0, 180.0, 360.0]
        assert [120.0, 180.0, 360.0] == [120.0, 180.0...300.0, 3360.0]
          Right contains 3 more items, first extra item: 3240.0

    RED under mutant "the set-aside panel leaves the rotation"
    (``_close_group_pass`` dropping every panel it set aside, as before H4),
    with the same two lines (observed).
    """
    night = await _night(group_hub, monkeypatch, grid_plan(),
                         goto=_misses(first=3))
    assert night.done
    ((set_at, line),) = _set_aside_lines(night)
    assert set_at == 540.0
    assert "set aside for now, not for the night" in line, line
    assert "a restart tonight does not retry it" not in line, line
    tried = _tried(night)
    expiry = set_at + SET_ASIDE_EXPIRY_S
    assert tried == FIRST_THREE + [expiry, expiry + 60.0, expiry + 120.0], (
        f"2-2 was tried at {tried}")

    expired = [(night.rel(t), m) for t, _lvl, m in night.lines
               if "set-aside has expired" in m]
    assert expired == [(set_at + SET_ASIDE_EXPIRY_S,
                        "M31: 2-2's set-aside has expired (45 minutes have "
                        "passed); it is tried once more tonight, and set aside "
                        "for the rest of the night if it fails as often "
                        "again")], expired
    assert not [m for _t, _l, m in night.flagged if "expired" in m]
    (record,) = night.stored.set_aside
    assert record == {"target_id": "p11", "step_id": None,
                      "reason": record["reason"], "night": night_key(T0),
                      "kind": CENTRING, "ts": T0 + set_at, "expired": True}
    assert night.stored.set_aside_on(night_key(T0)) == []
    assert (night.stored.owed(), night.stored.status) == (0, "complete")
    assert _skips(night) == []
    _assert_in_words(night)


async def test_a_mosaic_waiting_only_on_an_expiry_parks_and_waits_once(
        group_hub, monkeypatch):
    """At 540 s the other three panels are complete and 2-2's expiry is all
    the run has left. It is a waiter with an eta, not an end and not a spin:
    the long-wait park-hold stops tracking at once, the published state names
    the panel it waits for, the scheduler publishes once and sleeps to the
    expiry, and the run is still going then.

    RED under mutant "expiry waiter wakes every recheck" (the waiter's wake
    ``now + REACH_RECHECK_S`` instead of its expiry), observed:

        AssertionError: tracking was stopped at [660.0], not at the
        set-aside (540.0 s)
        assert [660.0] == [540.0]

    RED under mutant "the set-aside panel leaves the rotation"
    (``_close_group_pass`` dropping every panel it set aside, as before H4),
    observed:

        AssertionError: the run ended at 540.0 s, before 2-2's expiry
        assert 540.0 > 3240.0
    """
    night = await _night(group_hub, monkeypatch, grid_plan(),
                         goto=_misses(first=3))
    ((set_at, _line),) = _set_aside_lines(night)
    expiry = set_at + SET_ASIDE_EXPIRY_S
    end = max(e[0] for e in night.trace if e[1] == "state")
    assert end > expiry, (f"the run ended at {end} s, before 2-2's expiry")
    stops = [e[0] for e in night.trace
             if e[1] == "tracking" and e[2] is False]
    assert stops[:1] == [set_at], (
        f"tracking was stopped at {stops}, not at the set-aside ({set_at} s)")
    assert night.said("the next target is a long wait away")
    between = [e for e in night.trace
               if e[1] == "state" and set_at < e[0] < expiry]
    assert len(between) <= 2, (
        f"{len(between)} publishes while the run waited: {between[:3]}")
    details = {e[2].get("detail") for e in night.trace
               if e[1] == "state" and set_at <= e[0] < expiry}
    assert "M31: 2-2 is set aside for now; waiting to try it once more" in (
        details), details


# ------------------------- the rise half, dropped (backlog WP-07, #564)

def _low_plan() -> SequencePlan:
    """The 2x2 moved to the celestial equator, low in the east at 5 N:
    panels start near 26 degrees up and climb at about 15 degrees an hour."""
    members = [panel(r, c, ha_h=-4.3).model_copy(update={"dec_deg": 0.4 * r})
               for r in range(2) for c in range(2)]
    return SequencePlan(
        name="M31 mosaic", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False, targets=members,
        groups=[TargetGroup(id=GROUP_ID, name=GROUP_NAME,
                            geometry={"rows": 2, "cols": 2})])


async def test_a_panel_low_at_5_n_is_still_held_to_the_45_minutes(
        group_hub, group_store, monkeypatch):
    """AMENDED BY BACKLOG WP-07 (#564, 2026-09-30). This replaces
    ``test_a_panel_that_has_climbed_10_degrees_is_tried_before_45_minutes``:
    at 5 N the struck-out panel would have climbed ``SET_ASIDE_RISE_DEG`` in
    about 41 minutes under H4 orchestrator ruling 2 as first built, freeing
    it before the 45 minutes were up and, in doing so, telling a viewer the
    site sat within about 28 degrees of the equator. It no longer does:
    ``set_aside_expiry`` reads no altitude at all, so 2-2 is tried again only
    at its full 45-minute expiry, the same instant a panel at any other
    latitude would be, and no line about it is ever flagged ``site_derived``.

    RED under mutant "rise restored" (the rise branch and its altitude
    reads pasted back into ``group_rules.set_aside_expiry`` and engine
    ``_expire_or_wait``, the combined shape of H4 orchestrator ruling 2
    before this amendment), observed:

        AssertionError: 2-2 was tried again at 3240.0 s, before its 45
        minutes were up (3240.0 s)
        assert 3240.0 < 3240.0
    """
    lat = 5.0
    group_store.set_site(Site(name="Low fixture", latitude=lat, longitude=LON,
                              is_default=False))
    plan = _low_plan()
    night = await _night(group_hub, monkeypatch, plan, goto=_misses(first=3))
    assert night.done
    ((set_at, _line),) = _set_aside_lines(night)
    p = next(t for t in plan.targets if t.name == MISSES)

    def risen(rel: float) -> float:
        return (altaz(p.ra_hours, p.dec_deg, lat, LON, T0 + rel)[0]
                - altaz(p.ra_hours, p.dec_deg, lat, LON, T0 + set_at)[0])

    expiry = set_at + SET_ASIDE_EXPIRY_S
    # Premise: at this latitude the pre-amendment rule would have freed 2-2
    # early, inside the scheduler's 45-minute scan.
    would_have_freed = next(set_at + 60.0 * k for k in range(1, 46)
                            if risen(set_at + 60.0 * k) >= SET_ASIDE_RISE_DEG)
    assert would_have_freed < expiry, (
        "premise: the pre-amendment rule would have freed 2-2 early here")
    tried = _tried(night)
    assert tried[:3] == FIRST_THREE
    assert tried[3] >= expiry, (
        f"2-2 was tried again at {tried[3]} s, before its 45 minutes were "
        f"up ({expiry} s)")
    assert tried[3] == expiry
    assert not [m for _t, _l, m in night.flagged if "set-aside" in m], (
        "no set-aside line is site-derived any more, at any latitude")
    _assert_in_words(night)


# ----------------------------------------------------------- once a night

async def test_a_panel_struck_out_again_is_set_aside_for_the_night(
        group_hub, monkeypatch):
    """2-2 never centres. Set aside at 540 s, it is tried once more at its
    expiry, and three more strikes, 300 s apart as a lone panel's deferrals
    are, set it aside for the rest of the night: no second expiry, the run
    ends, the second set-aside's line says a restart does not retry it, and
    the report marks it skipped once. The session holds both records, the
    first marked expired; a same-night restart reads the night's one expiry
    and does not hop to 2-2 at all.

    RED under mutant "expiry every selection" (``_may_expire`` without the
    night's count): 2-2 expires every 45 minutes all night, so the run is
    still going at the harness's fake horizon, observed:

        assert False
         +  where False = <_group_harness.Night object at
        0x000002282EA3FDA0>.done

    RED under mutant "restart forgets the night's expiries" (``start``
    seeding no ``_expiries_tonight``), observed:

        AssertionError: the restart hopped to 2-2: [2040.0, 2340.0, 2640.0]
        assert ['M31 2-2', '...2', 'M31 2-2'] == []
    """
    night = await _night(group_hub, monkeypatch, grid_plan(),
                         goto=_misses())
    assert night.done
    lines = _set_aside_lines(night)
    set_at = lines[0][0]
    expiry = set_at + SET_ASIDE_EXPIRY_S
    tried = _tried(night)
    assert tried == FIRST_THREE + [expiry, expiry + 300.0, expiry + 600.0], (
        f"2-2 was tried at {tried}")
    assert [t for t, _m in lines] == [set_at, expiry + 600.0]
    assert "a restart tonight does not retry it, the next night does" in (
        lines[1][1]), lines[1][1]
    assert _skips(night) == [f"skipped {MISSES}"]
    first, second = night.stored.set_aside
    assert (first["kind"], first.get("expired"), second["kind"],
            second.get("expired")) == (CENTRING, True, CENTRING, None)
    tonight = night_key(T0)
    assert night.stored.set_aside_expiries_on(tonight) == {"p11": 1}
    assert night.stored.set_aside_on(tonight) == [second]
    assert (night.stored.status, night.stored.owed()) == ("dormant", 6)

    again = await _night(group_hub, monkeypatch, grid_plan(), t0=RESTART,
                         session=night.stored, goto=_misses())
    assert again.done
    assert [who for _t, who in again.gotos] == [], (
        f"the restart hopped to 2-2: {_tried(again)}")
    assert again.said(f"{MISSES}: set aside earlier tonight (centring failed "
                      f"on 2-2 on 3 consecutive visits"), again.lines[-3:]
    assert again.said("a restart tonight does not retry it")


async def test_a_restart_before_the_expiry_neither_retries_nor_forgets(
        group_hub, monkeypatch):
    """The run is stopped at 900 s, 6 minutes after 2-2 was set aside for
    now, and restarted at 1200 s. The restart reads the record, with its kind
    and its time, and waits: 2-2 is not tried at once, and not held all
    night; it is tried at the set-aside's own expiry, centres (the sky has
    changed by 3000 s) and completes.

    RED under mutant "restart reads no kind" (``start`` keeping no kind from
    the record, so it reads as a set-aside for the night), observed:

        AssertionError: the restart tried 2-2 at []
        assert [] == [3240.0, 3300.0, 3360.0]
    """
    plan = grid_plan()
    goto = _misses(until=T0 + 3000.0)
    first = Night(group_hub, monkeypatch, goto=goto)
    first.arm()
    first.engine.start(plan)
    first.session_id = first.engine._session.id
    assert await first.until(T0 + 900.0)
    await first.engine.abort()
    await first.close()
    stored = session_store.load(first.session_id)
    (record,) = stored.set_aside
    assert (record["kind"], record["ts"], record.get("expired")) == (
        CENTRING, T0 + 540.0, None)

    again = await _night(group_hub, monkeypatch, plan, t0=T0 + 1200.0,
                         session=stored, goto=goto)
    assert again.done
    # From the FIRST run's start, where the set-aside's 540 s were counted
    # (``again.rel`` counts from the restart, 1200 s later).
    tried = [round(t - T0, 3) for t, who in again.gotos if who == MISSES]
    expiry = 540.0 + SET_ASIDE_EXPIRY_S
    assert tried == [expiry, expiry + 60.0, expiry + 120.0], (
        f"the restart tried 2-2 at {tried}")
    assert again.said(f"{MISSES}: set aside earlier tonight (centring failed "
                      f"on 2-2 on 3 consecutive visits: plate solve failed "
                      f"— used raw GoTo); it is tried once more tonight when "
                      f"its set-aside expires")
    assert (again.stored.owed(), again.stored.status) == (0, "complete")


async def test_a_mosaic_set_aside_whole_takes_a_panel_set_aside_for_now(
        group_hub, monkeypatch):
    """2-2 never centres and is set aside for now at 540 s; the other three
    owe six rounds each. From 600 s no frame is shot at all (``_run_step``
    shoots nothing), so the next pass takes no exposure and the group
    anti-spin sets the whole mosaic aside for the night. 2-2 goes with it:
    it leaves the rotation, the report marks it skipped once, and its
    latest record is the group's, so a restart later tonight, after 2-2's
    45 minutes are up, hops to no panel at all, as the mosaic's line says.

    RED under mutant "a pending expiry left out of the group's set-aside"
    (``_end_pending_expiries`` changing only the run's memory, as first
    built, so the session keeps the centring record alone for a restart to
    expire), at the record, observed:

        AssertionError: [{'kind': 'centring', 'night': '2026-09-01',
        'reason': 'centring failed on 2-2 on 3 consecutive visits: plate
        solve failed — used raw GoTo', 'step_id': None, ...}]
        assert ['centring'] == ['centring', 'group']

    and with that record check taken out, at the restart (observed):

        AssertionError: the restart hopped to [4500.0, 4800.0, 5100.0]
        assert ['M31 2-2', '...2', 'M31 2-2'] == []
    """
    real = engine_mod.SequenceEngine._run_step

    async def run_step(self, *a, **kw):
        if engine_mod.time.time() >= T0 + 600.0:
            return None
        return await real(self, *a, **kw)

    monkeypatch.setattr(engine_mod.SequenceEngine, "_run_step", run_step)
    plan = grid_plan(panel_kw={"count": 6})
    night = await _night(group_hub, monkeypatch, plan, goto=_misses())
    assert night.done, night.lines[-3:]
    assert [t for t, _m in _set_aside_lines(night)] == [540.0]
    assert night.said("a full pass over 3 panels took no exposures")
    assert _skips(night).count(f"skipped {MISSES}") == 1, _skips(night)
    mine = [r for r in night.stored.set_aside if r["target_id"] == "p11"]
    assert [r["kind"] for r in mine] == [CENTRING, "group"], mine
    assert not any(r.get("expired") for r in mine)

    again = await _night(group_hub, monkeypatch, plan, t0=RESTART,
                         session=night.stored, goto=_misses())
    assert again.done
    assert RESTART - T0 > 540.0 + SET_ASIDE_EXPIRY_S, "premise: 2-2 is due"
    assert [who for _t, who in again.gotos] == [], (
        f"the restart hopped to {[round(t - T0, 3) for t, _w in again.gotos]}")
    assert again.said(f"{MISSES}: set aside earlier tonight (a full pass")


# ------------------------------------------------------------ no site saved

async def test_with_no_site_saved_only_the_45_minutes_apply(
        group_hub, group_store, monkeypatch):
    """No saved site: the default the config starts with. The expiry is the
    time half alone, at exactly 45 minutes, and nothing in the run computes
    an altitude (the one function every altitude goes through,
    ``catalog.altaz``, is never called), and nothing crashes.

    RED under mutant "an unsaved site read as a site" (``_expire_or_wait``
    computing its altitudes with ``catalog.altaz`` at the config's 0, 0
    instead of through ``_frame_altitude``), observed:

        AssertionError: 4 altitudes were computed with no site saved
        assert 4 == 0
    """
    group_store.cfg().site = Site()
    calls: list[tuple] = []
    real = catalog_mod.altaz

    def spy(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(catalog_mod, "altaz", spy)
    night = await _night(group_hub, monkeypatch, grid_plan(),
                         goto=_misses(first=3))
    assert night.done, night.trace[-3:]
    ((set_at, _line),) = _set_aside_lines(night)
    expiry = set_at + SET_ASIDE_EXPIRY_S
    assert _tried(night) == FIRST_THREE + [expiry, expiry + 60.0,
                                           expiry + 120.0]
    assert len(calls) == 0, (
        f"{len(calls)} altitudes were computed with no site saved")
    assert night.stored.status == "complete"


# ------------------------------------------------------------------ controls

async def test_a_floor_or_a_reject_set_aside_never_expires(
        group_hub, monkeypatch):
    """CONTROL. 1-2 sinks below its own floor at its first frame (set aside,
    kind ``floor``); 2-1 rejects every frame, so its reject guards set its
    L and R aside (kind ``rejects``, one record a step), and then the panel,
    every filter it owes being set aside (kind ``steps``). None is what the
    passing hour clears, so neither panel is tried again: the run ends when
    1-1 and 2-2 complete, long before 45 minutes, and each is marked
    skipped.

    RED under mutant "every set-aside expires" (``_may_expire`` without the
    kind): each set-aside then reads as one for now, so the report never
    marks either panel skipped, observed:

        AssertionError: assert [] == ['skipped M31...pped M31 2-1']
          Right contains 2 more items, first extra item: 'skipped M31 1-2'
    """
    plan = grid_plan(count_mode="accepted", min_stars=5)
    sinker = next(t for t in plan.targets if t.name == _name("1-2"))
    from astrodeck.sequence.models import Schedule
    sinker.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda t, site, when: (10.0 if t.name == _name("1-2")
                               and len([c for c in night.captures
                                        if c["target"] == t.name]) >= 1
                               else real(t, site, when)))
    night = Night(group_hub, monkeypatch,
                  stars=lambda who, f: 0 if who == _name("2-1") else 50)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    stored = session_store.load(night.session_id)
    assert night.done
    kinds = {(r["target_id"], r["step_id"]): r["kind"]
             for r in stored.set_aside}
    assert kinds == {("p01", None): "floor", ("p10", "p10-L"): "rejects",
                     ("p10", "p10-R"): "rejects", ("p10", None): "steps"}, (
        stored.set_aside)
    assert not any(r.get("expired") for r in stored.set_aside)
    end = max(e[0] for e in night.trace if e[1] == "state")
    assert end < SET_ASIDE_EXPIRY_S, f"the run went on to {end} s"
    assert sorted(_skips(night)) == [f"skipped {_name('1-2')}",
                                     f"skipped {_name('2-1')}"]
    assert stored.owed() > 0 and stored.status == "dormant"


# ------------------------------------------------ the resume arm (no ephemeris)

def _resume_session() -> Session:
    """A two-panel mosaic, 1-1 complete, 1-2 owing: the only work tonight."""
    group = TargetGroup(id="g", name="M31", geometry={"rows": 1, "cols": 2})
    panels = [Target(id=f"p{c}", name=f"M31 1-{c + 1}", ra_hours=0.7,
                     dec_deg=41.0 + 0.3 * c, center=True, mosaic_group="g",
                     panel_row=0, panel_col=c,
                     steps=[ExposureStep(id=f"s{c}", filter="L",
                                         exposure_s=30.0, count=2)])
              for c in range(2)]
    from astrodeck.sequence.session import SessionFrame
    plan = SequencePlan(name="resume", targets=panels, groups=[group])
    return Session(id="s-resume", name="resume", status="dormant", plan=plan,
                   auto_resume=True,
                   frames=[SessionFrame(ts=T0 - 60.0, night="n",
                                        target_id="p0", step_id="s0")
                           for _ in range(2)])


def test_a_crash_resume_is_not_held_all_night_by_an_expired_set_aside():
    """The run died after setting 1-2 aside for now, before its expiry, so
    the record was never marked. The resume arm applies the time half
    itself: until ``SET_ASIDE_EXPIRY_S`` after the set-aside nothing tonight
    can be shot (``NOTHING_TONIGHT``, the hold), and from that instant 1-2
    is the panel to re-centre on and the session starts. It asks no
    ephemeris (no site is handed in), so the rise half is the run's.

    RED under mutant "resume ignores the expiry" (``recentre_candidates``
    reading ``Session.set_aside_on`` as it stands), observed:

        AssertionError: at the expiry the resume still holds: []
        assert [] == ['p1']
    """
    s = _resume_session()
    tonight = night_key(T0)
    s.note_set_aside("p1", "centring failed on 1-2 on 3 consecutive visits",
                     night=tonight, kind=CENTRING, ts=T0)
    before = recentre_candidates(s, tonight, now=T0 + SET_ASIDE_EXPIRY_S - 1)
    assert before == [] and nothing_to_shoot_tonight(s, before)
    at = recentre_candidates(s, tonight, now=T0 + SET_ASIDE_EXPIRY_S)
    assert [t.id for t in at] == ["p1"], (
        f"at the expiry the resume still holds: {[t.id for t in at]}")
    assert not nothing_to_shoot_tonight(s, at)


@pytest.mark.parametrize("case", ["floor", "no kind", "expired once already",
                                  "no clock"])
def test_the_resume_arm_expires_nothing_else(case):
    """CONTROLS: a floor set-aside, a record written before H4 (no kind),
    the second centring set-aside of a night whose first expired, and a
    resume asked with no clock: each still holds the panel hours later."""
    s = _resume_session()
    tonight = night_key(T0)
    now: float | None = T0 + 4 * 3600.0
    if case == "floor":
        s.note_set_aside("p1", "1-2 sank below its own altitude floor",
                         night=tonight, kind="floor", ts=T0)
    elif case == "no kind":
        s.note_set_aside("p1", "centring failed on 1-2", night=tonight)
    elif case == "expired once already":
        s.note_set_aside("p1", "centring failed", night=tonight,
                         kind=CENTRING, ts=T0 - 7200.0)
        s.note_set_aside_expired("p1", night=tonight)
        s.note_set_aside("p1", "centring failed", night=tonight,
                         kind=CENTRING, ts=T0)
    else:
        s.note_set_aside("p1", "centring failed", night=tonight,
                         kind=CENTRING, ts=T0)
        now = None
    assert recentre_candidates(s, tonight, now=now) == []
    assert len(standing_set_asides(s, tonight, now)) == 1


# ----------------------------------------------------------- the session

def test_the_record_gains_kind_ts_and_an_expiry_marker_additively():
    """Written with a kind and a time, the record carries both; written
    without, it keeps the spec's four keys (a reader treats it as never
    expiring). An expiry marks the standing centring record of that panel,
    which ``set_aside_on`` then leaves out and ``set_aside_expiries_on``
    counts; a floor record is never marked. The schema stays 1.

    RED under mutant "expired still read" (``set_aside_on`` ignoring the
    marker), observed:

        AssertionError: assert ['a', 'b', 'c'] == ['a', 'b']
          Left contains one more item: 'c'
    """
    from astrodeck.sequence.session import SESSION_SCHEMA
    s = Session()
    night = "2026-09-01"
    plain = s.note_set_aside("a", "floor", night=night)
    assert set(plain) == {"target_id", "step_id", "reason", "night"}
    floor = s.note_set_aside("b", "sank", night=night, kind="floor", ts=T0)
    rec = s.note_set_aside("c", "centring failed", night=night,
                           kind=CENTRING, ts=T0)
    assert (rec["kind"], rec["ts"]) == (CENTRING, T0)
    assert s.note_set_aside_expired("b", night=night) is None
    assert s.note_set_aside_expired("c", night=night) is rec
    assert rec["expired"] is True
    assert [r["target_id"] for r in s.set_aside_on(night)] == ["a", "b"]
    assert s.set_aside_on(night) == [plain, floor]
    assert [r for r in s.set_aside_on(night) if r.get("expired")] == []
    assert s.set_aside_expiries_on(night) == {"c": 1}
    assert s.set_aside_expiries_on("2026-09-02") == {}
    assert s.note_set_aside_expired("c", night=night) is None
    assert SESSION_SCHEMA == 1
    with pytest.raises(ValueError, match="finite"):
        s.note_set_aside("d", "x", night=night, kind=CENTRING,
                         ts=float("nan"))
