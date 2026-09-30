"""The mosaic group driver on the clocked simulator (#189 S2, task T10; spec
5.1 to 5.4, 5.6 steps 4 and 7, 5.9, 5.10, 6.4, 6.7, the S2 tests list).

Every case runs the REAL `_run_scheduled`, `_setup_target`, `_run_steps` and
`_run_step` against the simulator hub on a clocked fake time
(tests/_group_harness.py): nothing sleeps, a night of hours takes a second,
and no double reads live device state. What the harness scripts is the sky
(a frame's star count, which the real grader judges against the plan's
``min_stars``), the centring result and the guider's start, each per panel.

The fixture mosaic is a 2x2 of L and R, members in row-major plan order, so
the snake order a pass visits them in is the group's doing:
``1-1 1-2 2-2 2-1`` (panel_order, spec 5.2). Its group is "M31" and its
panels are the targets "M31 1-1" and so on.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/, never in the shared tree
(#254), and the file restored byte for byte.
"""
from __future__ import annotations

import json

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GOLDEN_T0, GOLDEN_TRACE_PATH, GROUP_NAME, T0,
                            Night, golden_flow_plan, grid_plan, group_hub,
                            group_store, plain_mosaic_plan, single)
from astrodeck.config import EscalationConfig
from astrodeck.events import night_key
from astrodeck.sequence.group_rules import DEFER_WAIT_S, VisitBound
from astrodeck.sequence.models import Instruction
from astrodeck.sequence.session import Session, SessionFrame, session_store
# The cloud-hold harness's fixtures, for the one case that runs a real hold.
from test_idle_park_hold import sim_hub, temp_store  # noqa: F401

SNAKE = ["1-1", "1-2", "2-2", "2-1"]


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _label(target: str) -> str:
    prefix = GROUP_NAME + " "
    return target[len(prefix):] if target.startswith(prefix) else target


def _labels(visits) -> list[str]:
    return [_label(t) for t, _f in visits]


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 session=None, on_capture=None, **kw) -> Night:
    """Run ``plan`` to its end on a fresh clocked night. The night comes back
    closed, with its trace, ``done`` (the run ended inside the harness's
    wall-clock bound and fake horizon) and its session as stored."""
    night = Night(hub, monkeypatch, **kw)
    night.on_capture = on_capture
    start_kw = {"session": session} if session is not None else {}
    try:
        night.done = await night.run(plan, wall_s=wall_s, **start_kw)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _gotos(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


def _expected_trace(name: str) -> str:
    data = json.loads(GOLDEN_TRACE_PATH.read_text(encoding="utf-8"))
    return "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n"
                   for e in data[name])


def _first_difference(got: str, want: str) -> str:
    g, w = got.splitlines(), want.splitlines()
    for i, (a, b) in enumerate(zip(g, w)):
        if a != b:
            return f"line {i + 1}:\n  got  {a[:300]}\n  want {b[:300]}"
    return f"lengths differ: got {len(g)} lines, want {len(w)}"


# ------------------------------------------------------------ the golden trace

#: What S3's compile moved in the golden flow plan's fixture, and the value
#: each held when the trace was recorded. S3-CP gave every TARGET its own
#: centring (#170: 1.2 arcmin and 3 tries, the hub's own 0.02 deg and 3, so
#: the run centres exactly as before), and S3-W made the flow count accepted
#: subs (Revision 2 ruling 2). Neither is the group driver this test grades.
S3_GOLDEN_PLAN = {"count_mode": ("accepted", "attempts")}
S3_GOLDEN_TARGET = {"center_tolerance_arcmin": (1.2, None),
                    "center_attempts": (3, None)}


def _golden_as_recorded():
    """The golden flow plan as it was when the trace was recorded: the
    fixture with S3's two re-pins put back.

    RE-PINNED IN THE INTEGRATION OF S3. Until then this test ran the fixture
    as it is, and S3's re-pin of ngc7331_quick.json turned it red on the
    first published state (``count_mode`` "accepted" against the recorded
    "attempts"). Putting the two values back, rather than re-recording the
    trace, keeps the trace what its "about" says it is (recorded before the
    S2 group driver). BOUNDED: the fixture must carry exactly S3's values, so
    any other drift of the fixture still reaches the byte comparison.

    RED under mutant "the fixture drifts once more" (in a private scratch
    copy of the fixture, the first target's ``center_attempts`` 3 made 4),
    observed:

        AssertionError: premise: the fixture holds S3's center_attempts
        assert 4 == 3
    """
    plan = golden_flow_plan()
    for key, (s3, before) in S3_GOLDEN_PLAN.items():
        assert getattr(plan, key) == s3, (
            f"premise: the fixture holds S3's {key}")
    updates = {k: before for k, (_s3, before) in S3_GOLDEN_PLAN.items()}
    targets = []
    for t in plan.targets:
        for key, (s3, before) in S3_GOLDEN_TARGET.items():
            assert getattr(t, key) == s3, (
                f"premise: the fixture holds S3's {key}")
        targets.append(t.model_copy(update={
            k: before for k, (_s3, before) in S3_GOLDEN_TARGET.items()}))
    return plan.model_copy(update={**updates, "targets": targets})


@pytest.mark.parametrize("name", ["golden", "plain"])
async def test_a_plan_without_groups_runs_byte_for_byte_as_before(
        group_hub, monkeypatch, name):
    """``groups == []`` (the golden flow plan) and three targets that share a
    ``mosaic_group`` with no ``groups`` entry reproduce, byte for byte, the
    trace recorded on the engine BEFORE the group driver existed
    (fixtures/group_golden_trace.json): every mount, camera and guider call,
    every published state and every log line, at the same fake instant
    (spec 3.4: an empty groups list gives a byte-identical run, and a
    mosaic_group with no groups entry keeps today's panel-first behaviour).
    This is #154's engine half as well: the plain mosaic shoots 1,1,2,2,3,3.

    MUTANT "requeue without a groups entry" (`_group_of` makes a default
    TargetGroup for a mosaic_group the plan does not carry): RED on "plain"
    (observed):
        AssertionError: line 1:
            got  [0.0,"state",{"detail":"starting plan 'plain mosaic'","keys":
        ["detail","plan_name","progress","session","sky","state"],"plan_name":
        "plain mosaic","progress":{"current_exposure_s":0.0,"elapsed_s":0,"eta
        _confident":false,"eta_s":1002,"events_cost_s":750,"frame_started_at_m
        s":null,"frames_done":0,"fram
            want [0.0,"state",{"detail":"starting plan 'plain mosaic'","keys":
        ["detail","plan_name","progress","session","sky","state"],"plan_name":
        "plain mosaic","progress":{"current_exposure_s":0.0,"elapsed_s":0,"eta
        _confident":false,"eta_s":552,"events_cost_s":300,"frame_started_at_ms
        ":null,"frames_done":0,"frame

    RE-PINNED IN S4-SIM (#320): the golden night starts at ``GOLDEN_T0``, an
    hour before ``T0``, and not at ``T0``. Every night now runs its hour
    angle on the night's clock, and on that clock the golden plan's 195 min
    from ``T0`` cross NGC 7331's meridian 180 min in: the engine held for the
    flip point at 9960 s and flipped, and the trace differed from line 341
    ("holding for the meridian flip point" against "dithering"). The trace
    was recorded while the countdown read the wall clock, which is why it
    passed at some hours of the day and held until the fake horizon at
    others. From ``GOLDEN_T0`` the night ends 34.6 min before the flip point
    and the recorded trace comes out byte for byte, unchanged, so it is
    still the one recorded before the group driver; test_group_golden_wall
    _clock.py runs it across the hours of the wall clock. The plain plan's
    targets are placed by hour angle at ``T0``, so it keeps ``T0``.

    MUTANT "the golden night from T0" (``t0 = T0`` for both plans): RED on
    "golden", "plain" green (observed):
        AssertionError: line 341:
            got  [9960.0,"state",{"detail":"holding for the meridian flip
        point","keys":["detail","plan_name","progress","session","sky",...
            want [9960.0,"state",{"detail":"dithering","keys":["detail",
        "plan_name","progress","session","sky",...
    """
    plan = (_golden_as_recorded() if name == "golden"
            else plain_mosaic_plan())
    t0 = GOLDEN_T0 if name == "golden" else T0
    night = await _night(group_hub, monkeypatch, plan, wall_s=120.0, t0=t0)
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    got, want = night.trace_text(), _expected_trace(name)
    assert got == want, _first_difference(got, want)
    assert not any("group" in s["keys"] for s in night.states), (
        "a run with no group published a group key")
    if name == "plain":
        assert [t for t, _f in night.shots()] == [
            "Plain 1", "Plain 1", "Plain 2", "Plain 2", "Plain 3", "Plain 3"]


# -------------------------------------------------------------- the rotation

async def test_a_2x2_rotates_one_pass_a_visit_in_snake_order(
        group_hub, monkeypatch):
    """A 2x2 of L and R, count 3, ``per_visit`` 1, one pass a visit: the
    visits go exactly ``1-1(L,R) 1-2 2-2 2-1``, three times over, each pass
    numbered in the published group state, and ``owed() == 0`` completes
    the session (spec 5.1, the S2 tests list).

    MUTANT "remove the requeue" (the `_requeue` call in `_visit_panel`
    deleted, so a visited panel keeps its place at the front): the order
    survives, because least-complete ordering at each boundary rebuilds it,
    but every pass is one visit long. RED (observed):
        AssertionError: a pass is one visit to every panel: [1, 2, 3, 4, 5, 6,
        7, 8, 9, 9, 9, 9]
    """
    night = await _night(group_hub, monkeypatch, grid_plan())
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert _labels(visits) == SNAKE * 3, _labels(visits)
    assert all(f == ("L", "R") for _t, f in visits), visits
    passes = [c["group"]["pass"] for c in night.captures if c["filter"] == "L"]
    assert passes == [1] * 4 + [2] * 4 + [3] * 4, (
        f"a pass is one visit to every panel: {passes}")
    assert night.stored.owed() == 0 and night.stored.status == "complete", (
        night.stored.status, night.stored.owed())
    assert night.engine.state.get("end_reason") == "complete"


@pytest.mark.parametrize("passes, hops", [(1, 16), (2, 8)])
async def test_two_passes_a_visit_halve_the_hops(group_hub, monkeypatch,
                                                 passes, hops):
    """Count 4 of L and R: at one pass a visit each panel is visited four
    times, 16 hops; at two passes a visit, twice, 8 hops, each visit L R L R
    (spec 5.3: ``rounds >= passes``).

    MUTANT "passes ignored" (`_visit_panel` builds ``VisitBound(passes=1,
    ...)`` whatever the group says): RED on passes=2 (observed):
        AssertionError: (16, ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...])
        assert 16 == 8
    """
    plan = grid_plan(group_kw={"visit_passes": passes},
                     panel_kw={"count": 4})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert len(night.gotos) == hops, (len(night.gotos), _labels(night.visits()))
    assert all(f == ("L", "R") * passes for _t, f in night.visits()), (
        night.visits())
    assert night.stored.owed() == 0


async def test_visit_min_s_extends_a_visit_at_round_boundaries(
        group_hub, monkeypatch):
    """One pass a visit, but ``visit_min_s`` 150 s against 60 s rounds (two
    30 s frames, no overhead on the fake clock): a round ends at 60 s and
    at 120 s short of the minimum, and at 180 s past it, so each visit is
    three rounds and each panel is done in one visit. The clock starts after
    the hop (spec 5.3).

    MUTANT "min_s ignored" (`_visit_panel` builds the VisitBound with
    ``min_s=0.0``): RED (observed):
        AssertionError: ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...]
        assert ['1-1', '1-2'...', '1-2', ...] == ['1-1', '1-2', '2-2', '2-1']
    """
    plan = grid_plan(group_kw={"visit_min_s": 150.0})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert _labels(visits) == SNAKE, _labels(visits)
    assert all(f == ("L", "R") * 3 for _t, f in visits), visits


# ------------------------------------------------------------ the first pass

def _seeded(plan, frames: list[tuple[str, str, float, bool]]) -> Session:
    """A dormant session of ``plan`` holding ``frames`` as ``(target id,
    step id, ts, accepted)``."""
    s = Session(name=plan.name, created_ts=T0 - 86400.0, status="dormant",
                plan=plan)
    for tid, sid, ts, ok in frames:
        s.frames.append(SessionFrame(ts=ts, night="n0", target_id=tid,
                                     step_id=sid, auto_accepted=ok))
    return s


async def test_uneven_progress_goes_least_complete_first_then_never_visited(
        group_hub, monkeypatch):
    """Accepted mode, a ledger seeded unevenly: 1-1 holds one accepted L
    (1/6 banked), 2-2 holds one REJECTED L (nothing banked, but visited),
    1-2 and 2-1 hold nothing. The first pass goes least complete first,
    ties to the never-visited, then the snake: 1-2, 2-1, 2-2, 1-1 (spec 5.2,
    5.9: the first pass is ordered from the ledger).

    MUTANT "the first pass keeps the plan's order" (the `_resort_group` call
    in `_start_groups` deleted): RED (observed):
        AssertionError: ['1-1', '1-2', '2-1', '2-2', '1-2', '2-1', ...]
        assert ['1-1', '1-2', '2-1', '2-2'] == ['1-2', '2-1', '2-2', '1-1']
    MUTANT "last visit from this run only" (the ledger's frame times left out
    of `_order_snapshot`): 2-2 then ties with the never-visited. RED
    (observed):
        AssertionError: ['1-2', '2-2', '2-1', '1-1', '1-2', '2-2', ...]
        assert ['1-2', '2-2', '2-1', '1-1'] == ['1-2', '2-1', '2-2', '1-1']
    """
    plan = grid_plan(count_mode="accepted")
    session = _seeded(plan, [("p00", "p00-L", T0 - 7200.0, True),
                             ("p11", "p11-L", T0 - 3600.0, False)])
    night = await _night(group_hub, monkeypatch, plan, session=session)
    assert night.done, night.trace[-3:]
    assert _labels(night.visits())[:4] == ["1-2", "2-1", "2-2", "1-1"], (
        _labels(night.visits()))


async def test_a_resume_mid_group_starts_on_the_least_complete_panel(
        group_hub, monkeypatch):
    """A resume from a mid-group ``done_map``: 1-1 complete (6 of 6), 1-2 at
    4 of 6, 2-2 at 2 of 6, 2-1 at 1 of 6. The run starts on 2-1, the least
    complete, then 2-2, then 1-2, and never goes back to 1-1 (spec 5.9: no
    cursor to lose).

    MUTANT "the first pass keeps the plan's order": RED (observed):
        AssertionError: ['1-2', '2-1', '2-2', '2-1', '2-2', '2-1']
        assert ['1-2', '2-1', '2-2'] == ['2-1', '2-2', '1-2']
    """
    plan = grid_plan()
    have = {"p00": 3, "p01": 2, "p11": 1}
    frames = [(tid, f"{tid}-{f}", T0 - 3600.0, True)
              for tid, n in have.items() for f in ("L", "R") for _ in range(n)]
    frames.append(("p10", "p10-L", T0 - 3600.0, True))
    night = await _night(group_hub, monkeypatch, plan,
                         session=_seeded(plan, frames))
    assert night.done, night.trace[-3:]
    labels = _labels(night.visits())
    assert labels[:3] == ["2-1", "2-2", "1-2"], labels
    assert "1-1" not in labels, labels
    assert night.stored.owed() == 0


async def test_setting_first_visits_the_panel_below_its_own_floor_soonest(
        group_hub, monkeypatch):
    """``order = "setting_first"``: panel 2-1 carries its own floor at 30
    degrees (``on_floor = advance``) and reads above it for the first hour
    and below it after; the other panels reach no floor tonight. Every pass
    goes to 2-1 first, then to the others by fraction banked and the snake
    (spec 5.2). How long each panel has is read inside the engine only.

    MUTANT "no setting data" (`_order_snapshot` never filling
    ``time_to_floor_s``): the order falls back to the snake and 2-1 goes
    last. RED (observed):
        AssertionError: ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...]
        assert ['1-1', '1-2'...', '1-2', ...] == ['2-1', '1-1'...', '1-1',
        ...]
          At index 0 diff: '1-1' != '2-1'
    MUTANT "own floor ignored" (the ``on_floor`` floor left out of
    `_time_to_floor_s`): RED (observed):
        AssertionError: ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...]
        assert ['1-1', '1-2'...', '1-2', ...] == ['2-1', '1-1'...', '1-1',
        ...]
          At index 0 diff: '1-1' != '2-1'
    """
    from astrodeck.sequence.models import Schedule
    plan = grid_plan(group_kw={"order": "setting_first"})
    p21 = next(t for t in plan.targets if t.name == _name("2-1"))
    p21.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda t, site, when: ((40.0 if when < T0 + 3600.0 else 10.0)
                               if t.name == _name("2-1")
                               else real(t, site, when)))
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _labels(night.visits()) == ["2-1", "1-1", "1-2", "2-2"] * 3, (
        _labels(night.visits()))


async def test_setting_first_reads_the_mounts_floor_too(
        group_hub, group_store, monkeypatch):
    """The mount's own floor counts as well: with a 20 degree floor, and
    panel 2-2 moved three hours west of the others, 2-2 sets hours before
    them and is visited first (spec 5.2: "time until the panel reaches its
    floor or the horizon mask").

    MUTANT "mount floor ignored" (`_time_to_floor_s` not asking
    `_altitude_limit_verdict`): RED (observed):
        AssertionError: ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...]
        assert '1-1' == '2-2'
    """
    from astrodeck.config import SafetyConfig
    from _group_harness import ra_at
    group_store.set_safety(SafetyConfig(enabled=False, min_alt_deg=20.0))
    plan = grid_plan(group_kw={"order": "setting_first"})
    p22 = next(t for t in plan.targets if t.name == _name("2-2"))
    p22.ra_hours = ra_at(1.0)
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _labels(night.visits())[0] == "2-2", _labels(night.visits())


# -------------------------------------------------------------- set aside

async def test_the_floor_on_one_panel_sets_only_that_panel_aside_for_tonight(
        group_hub, monkeypatch):
    """Panel 2-2 carries ``on_floor = advance`` at 30 degrees and reads 10
    degrees to the frame loop's floor check; the others have no floor. Only
    2-2 is set aside, before a frame is shot on it, recorded in
    ``Session.set_aside`` with tonight's night key and no step; the other
    three complete (spec 5.1 outcome table, 5.8, 6.7).

    MUTANT "the floor sets the whole group aside" (`_visit_panel`'s
    FloorStop arm calling ``run.set_aside_all``): RED (observed):
        AssertionError: ('1-1', [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31
        1-2', 'L'), ('M31 1-2', 'R')])
        assert ['L', 'R'] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    plan = grid_plan()
    floored = next(t for t in plan.targets if t.name == _name("2-2"))
    from astrodeck.sequence.models import Schedule
    floored.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    real = engine_mod._frame_altitude
    monkeypatch.setattr(
        engine_mod, "_frame_altitude",
        lambda target, site, when: (10.0 if target.name == _name("2-2")
                                    else real(target, site, when)))
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _shot(night, "2-2") == [], night.shots()
    for label in ("1-1", "1-2", "2-1"):
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.shots())
    records = night.stored.set_aside
    assert [(r["target_id"], r["step_id"]) for r in records] == [
        ("p11", None)], records
    assert records[0]["night"] == night_key(T0), records
    assert "floor" in records[0]["reason"], records


async def test_a_panel_that_will_not_centre_is_deferred_three_times_then_set_aside(
        group_hub, monkeypatch):
    """Panel 3 of the snake, 2-2, never centres, under ``require_centred``
    (the default). It is deferred on passes 1 and 2, shooting nothing, and
    at its third consecutive failure it is set aside for tonight with a
    WARNING that names it, the cause and the last error (spec 5.1, 6.8).
    The other panels complete.

    MUTANT "no failure counter" (`GroupRun._count_failure` never adds to
    ``failed``, group_rules.py): 2-2 is deferred for ever, and once the
    others are done the run waits out deferral after deferral. RED
    (observed):
        AssertionError: (203, [(1788371229.0, 'info', 'M31: pass 194 took no
        exposures and deferred 1 visits; waiting 300 s before the next
        pa...'M31 mosaic': stopped by hand, so auto-resume is disarmed for it.
        Arm it from the session list to pick it up again.")])
        assert False
         +  where False = <_group_harness.Night object at
        0x000001DBD8C38C80>.done

    RE-PINNED FOR H4 (#534, H4 orchestrator ruling 2). 2-2's misses are
    its own (its neighbours centre), and its third sets it aside, but a
    streak of centring misses now sets it aside "for now": the set-aside
    expires 45 minutes on (2-2 is hopped again at 3240 s), it strikes out
    again, and that second streak is the one set aside for tonight, with
    the WARNING this case reads. So 2-2 is hopped six times, not three, the
    session holds two records (the first marked expired), and there is
    still exactly one "set aside for tonight" line. The same mutant, re-run
    by the H4 integration in a private copy of server/ (scratchpad
    H4-INTEG-mut), is still RED at the fake horizon, with the failure
    recorded above word for word ("(203, [(1788371229.0, 'info', 'M31: pass
    194 took no exposures ...").
    """
    def goto(who, n, result):
        if who == _name("2-2"):
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    assert night.done, (len(night.gotos), night.lines[-3:])
    assert _shot(night, "2-2") == [], night.shots()
    assert len(_gotos(night, "2-2")) == 6, _gotos(night, "2-2")
    alerts = [(lvl, m) for _t, lvl, m in night.lines
              if "2-2" in m and "set aside for tonight" in m]
    assert len(alerts) == 1 and alerts[0][0] == "warning", alerts
    assert ("centring failed on 2-2 on 3 consecutive visits: plate solve "
            "failed" in alerts[0][1]), alerts
    for label in ("1-1", "1-2", "2-1"):
        assert _shot(night, label) == ["L", "R"] * 3, label
    assert [(r["target_id"], bool(r.get("expired")))
            for r in night.stored.set_aside] == [("p11", True),
                                                 ("p11", False)]


async def test_control_a_group_that_does_not_require_centring_shoots_the_panel(
        group_hub, monkeypatch):
    """The control for the case above: ``require_centred`` off, the same
    panel 2-2 that never centres is shot where the raw GoTo put it, with
    today's "continuing" warning, on every visit, and nothing is deferred or
    set aside (spec 5.6 step 4: the deferral is ``require_centred``'s). Added
    by the T10 verifier (#286): no control showed it.

    MUTANT "require_centred ignored" (the ``require_centred`` condition
    dropped from `_setup_target`'s miss and from `_group_hop_checks`): 2-2 is
    deferred three times and set aside. RED (observed):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-1', 'L'), ('M31 2-1', 'R'), ...]
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    def goto(who, n, result):
        if who == _name("2-2"):
            return {**result, "centered": False, "error_arcmin": None}
        return result

    plan = grid_plan(group_kw={"require_centred": False})
    night = await _night(group_hub, monkeypatch, plan, goto=goto)
    assert night.done, night.trace[-3:]
    assert _shot(night, "2-2") == ["L", "R"] * 3, night.shots()
    assert len(night.said("M31 2-2: centering plate solve failed — used raw "
                          "GoTo — continuing")) == 3, night.lines[-6:]
    assert night.stored.set_aside == [], night.stored.set_aside
    assert night.stored.owed() == 0


@pytest.mark.parametrize("flag", ["rotation_skipped", "rotation_unavailable"])
async def test_a_rotation_that_did_not_happen_defers_the_panel(
        group_hub, monkeypatch, flag):
    """A rotating mosaic (``rotate`` set, members at PA 30): the hop's
    centring comes back with ``flag`` on panel 1-2's first visit. That visit
    shoots nothing and 1-2 is retried first on the next pass, at visit 4
    counted from 0 (``visits()[4]``), where it shoots (spec 5.6 step 4, 5.1
    pass boundary item 3). The control is a group that does not rotate: the
    same flag defers nothing, and its night runs to its end.

    MUTANT "rotation flags ignored" (the two rotation checks in
    `_group_hop_checks` deleted): RED on both flags (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')),
        ('M31 2-2', ('L', 'R')), ('M31 2-1', ('L', 'R')), ('M31 1-1', ('L',
        'R'))]
        assert ('M31 1-2', ('L', 'R')) == ('M31 1-2', ())
    MUTANT "no re-sort at the pass boundary" (the `_resort_group` call at
    the end of `_close_group_pass` deleted, #318): pass 2 keeps pass 1's
    order and 1-2 is retried at visit 5, which the ">= 4" this case had
    before #318 let through. RED on both flags (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ()), ('M31
        2-2', ('L', 'R')), ('M31 2-1', ('L', 'R')), ('M31 1-1', ('L', 'R')),
        ('M31 1-2', ('L', 'R')), ...]
        assert ([5, 9, 12] and 5 == 4)
    MUTANT "the control night cut short" (the control's `_night` given
    ``horizon_s=200.0``, so it stops in its fourth visit of twelve): RED on
    both flags (observed), where the control without its ``night.done``
    line passed both:
        AssertionError: [[180.0, 'slew', 21.618021, 40.4], [180.0, 'state',
        {'detail': 'M31 2-1: L 30s  [1/3]', 'group': {'id': 'm31-mosaic',
        ...rogress', 'session', 'sky', ...], 'plan_name': 'M31 mosaic', ...}],
        [180.0, 'capture', 'M31 2-1', 'L', 30.0, 100, ...]]
        assert False
         +  where False = <_group_harness.Night object at
        0x0000024FAA83CB30>.done
    """
    def goto(who, n, result):
        if who == _name("1-2") and n == 1:
            return {**result, flag: True}
        return result

    plan = grid_plan(group_kw={"rotate": True, "pa_deg": 30.0})
    night = await _night(group_hub, monkeypatch, plan, goto=goto)
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert visits[1] == (_name("1-2"), ()), visits[:5]
    first = [i for i, (t, f) in enumerate(visits) if t == _name("1-2") and f]
    # THE RETRY IS THE NEXT PASS'S FIRST VISIT, visit 4 counted from 0
    # (#318; spec 5.1 pass boundary item 3). At the boundary 1-2 holds
    # nothing and every other panel one L and one R, so the re-sort, least
    # complete first, starts pass 2 on 1-2. Without the re-sort the requeue
    # leaves pass 2 in pass 1's order and 1-2 comes second, at visit 5,
    # which ">= 4" let through.
    assert first and first[0] == 4, visits
    assert _shot(night, "1-2") == ["L", "R"] * 3

    control = grid_plan()
    night = await _night(group_hub, monkeypatch, control, goto=goto)
    assert night.done, night.trace[-3:]
    assert night.visits()[1] == (_name("1-2"), ("L", "R")), night.visits()[:3]


async def test_an_all_deferred_pass_waits_through_the_safety_gate(
        group_hub, monkeypatch):
    """Every panel's first hop fails to centre: pass 1 takes no exposure and
    defers four panels, so the boundary waits ``DEFER_WAIT_S`` through
    `_wait_until`, with the safety gate asked on every tick, before pass 2,
    where every panel centres (spec 5.1 pass boundary, item 2).

    MUTANT "no deferral wait" (the `_wait_until` call in
    `_close_group_pass` deleted): RED (observed):
        AssertionError: pass 2 began 0 s after pass 1
        assert 0.0 >= 300.0

    SINCE H4 (#534, H4 orchestrator ruling 2) this pass, every panel tried
    missing its centring, is the centring hold: no panel is struck, and the
    group waits ``CENTRING_HOLD_RETRY_S`` (600 s), not ``DEFER_WAIT_S``,
    through the same wait path and safety gate. The bounds below are
    ``DEFER_WAIT_S`` lower bounds and still hold; the hold's own length is
    graded in test_h4_centring_group_hold.py. Noted by the H4 integration.
    """
    def goto(who, n, result):
        return {**result, "centered": n > 1,
                "error_arcmin": None if n == 1 else 0.2}

    night = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    assert night.done, night.trace[-3:]
    hops = [t for t, _who in night.gotos]
    assert len(hops) == 4 + 12, len(hops)
    last_deferred, first_retry = hops[3], hops[4]
    gap = first_retry - last_deferred
    assert gap >= DEFER_WAIT_S, f"pass 2 began {gap:.0f} s after pass 1"
    # The wait's own ticks: the "frame" gate `_wait_until` asks every 5 s,
    # from the moment pass 1's last deferral ends (no frame was shot in
    # between, so no other frame gate falls in the window).
    ticks = [t for t, ctx in night.gates
             if last_deferred <= t < first_retry and ctx == "frame"]
    assert len(ticks) >= DEFER_WAIT_S / engine_mod.SCHEDULE_WAIT_STEP_S, (
        f"the safety gate was asked {len(ticks)} times in the wait")
    assert night.stored.owed() == 0


async def test_a_pass_that_takes_no_exposure_ends_the_group(
        group_hub, monkeypatch):
    """No frame can be taken on any panel (a `_run_step` that exposes
    nothing, as a wheel with none of the filters would): one pass of four
    visits takes no exposure and defers nothing, so the group anti-spin sets
    every panel aside for tonight and the run ends (spec 5.1 pass boundary,
    item 1; 6.4).

    MUTANT "skip the group anti-spin" (`group_rules.pass_boundary` answering
    ``"next_pass"`` for a pass with no exposures and no deferrals): the
    driver hops the four panels for ever. RED at the harness's wall-clock
    bound (observed):
        AssertionError: the run was still hopping at the harness's wall-clock
        bound: 20933 hops
    """
    import asyncio

    async def exposes_nothing(self, ti, si, target, step, max_frames=None):
        await asyncio.sleep(0)

    monkeypatch.setattr(engine_mod.SequenceEngine, "_run_step", exposes_nothing)
    night = await _night(group_hub, monkeypatch, grid_plan(), wall_s=15.0)
    assert night.done, (
        f"the run was still hopping at the harness's wall-clock bound: "
        f"{len(night.gotos)} hops")
    assert _labels(night.visits()) == SNAKE, _labels(night.visits())
    reasons = {r["reason"] for r in night.stored.set_aside}
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01", "p10", "p11"], night.stored.set_aside
    assert reasons == {"a full pass over 4 panels took no exposures; setting "
                       "the mosaic aside for tonight"}, reasons
    assert night.engine.state.get("end_reason") == "incomplete"


# ----------------------------------------------------------- the reject rule

async def test_a_panel_that_rejects_alone_is_set_aside_after_three_visits(
        group_hub, monkeypatch):
    """Accepted mode with a star floor: 2-2's frames carry no stars and are
    rejected by the real grader, every other panel's are accepted. After
    three visits that accept nothing while the others accept, 2-2 is set
    aside for tonight with a warning; its reject guard (5 a step) has not
    tripped by then (spec 5.1 outcome table, row 3).

    MUTANT "count all zero-accept visits" (the "another live member accepted
    since" condition in `GroupRun.visit_outcome` dropped, group_rules.py):
    RED in the control below, which it sets aside whole (observed):
        AssertionError: the per-panel reject rule set a panel aside in a sky
        of rejects
          Left contains 4 more items, first extra item: 'M31: 1-1 rejected
        every frame for 3 visits while the other panels were accepted; set
        aside for tonight: a restart tonight does not retry it, the next night
        does'
    """
    plan = grid_plan(count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=5,
                     max_consecutive_rejects_night=0)
    night = await _night(group_hub, monkeypatch, plan,
                         stars=lambda who, f: 0 if who == _name("2-2") else 50)
    assert night.done, night.trace[-3:]
    assert len(_gotos(night, "2-2")) == 3, _gotos(night, "2-2")
    said = night.said("2-2 rejected every frame for 3 visits while the other "
                      "panels were accepted")
    assert len(said) == 1, night.lines[-6:]
    assert [r["target_id"] for r in night.stored.set_aside] == ["p11"]
    for label in ("1-1", "1-2", "2-1"):
        assert _shot(night, label) == ["L", "R"] * 3


async def test_control_when_every_panel_rejects_none_is_set_aside_by_the_rule(
        group_hub, monkeypatch):
    """Every panel rejects: the sky is to blame, so the per-panel rule moves
    no counter, and the reject guards own it: each step is set aside after
    its fifth consecutive reject, and each panel with every owed filter set
    aside is set aside for that (spec 5.1: "When every member rejects, the
    sky is to blame and the counter does not move")."""
    plan = grid_plan(count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=5,
                     max_consecutive_rejects_night=0)
    night = await _night(group_hub, monkeypatch, plan,
                         stars=lambda who, f: 0)
    assert night.done, night.trace[-3:]
    assert night.said("rejected every frame for") == [], (
        "the per-panel reject rule set a panel aside in a sky of rejects")
    assert all(len(_gotos(night, lb)) == 5 for lb in SNAKE), night.gotos
    panels = [r for r in night.stored.set_aside if r["step_id"] is None]
    assert len(panels) == 4 and all(
        "still owes is set aside" in r["reason"] for r in panels), panels


async def test_accepted_mode_with_a_rejecting_grader_terminates(
        group_hub, monkeypatch):
    """Accepted mode, every frame rejected, the night guard off and the step
    guard at 3: the carried per-step counter sets each filter aside on its
    panel's third visit, each panel is then set aside, and the run ends
    rather than hopping a sky of rejects all night (spec 5.3, 6.4).

    MUTANT "a visit resets the step's reject counter" (``_step_rejects``
    cleared at the top of `_run_visit`, the S0 local counter back): no step
    ever reaches 3 at one attempt a visit. RED at the harness's bound
    (observed):
        AssertionError: still running after 521 hops
    """
    plan = grid_plan(count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=3,
                     max_consecutive_rejects_night=0)
    night = await _night(group_hub, monkeypatch, plan, wall_s=30.0,
                         stars=lambda who, f: 0)
    assert night.done, f"still running after {len(night.gotos)} hops"
    steps = [r for r in night.stored.set_aside if r["step_id"] is not None]
    assert len(steps) == 8, steps
    assert night.engine.state.get("end_reason") == "incomplete"


# ------------------------------------------------------- the guider (ruling 5)

def _guide_plan(**kw):
    return grid_plan(guide=True, **kw)


@pytest.mark.parametrize("action", ["warn", "skip", "abort"])
async def test_one_panel_whose_guider_will_not_start_is_deferred_not_shot(
        group_hub, group_store, monkeypatch, action):
    """Under ``require_guiding`` with each ``guiding_action`` in turn, panel
    1-2's guider never starts; the others' do. 1-2 is deferred, never shot
    unguided, retried on each later pass after every other panel has had
    its visit, and set aside for tonight after three consecutive failed
    passes with a warning naming it and the last error. The run goes on:
    ``guiding_action`` is not asked (owner ruling 5, #142; spec 5.6 step 7).

    MUTANT "honour guiding_action per panel" (`_setup_target` never defers a
    member's failed start): warn shoots 1-2 unguided, skip sets it aside at
    once, abort ends the run. RED on each (observed):
        warn:
            AssertionError: ('complete', 'all targets complete')
            assert 'complete' == 'incomplete'
        skip:
            AssertionError: [60.0]
            assert 1 == 3
        abort:
            AssertionError: ('unsafe', 'guiding required but failed to start:
            no guide star found (attempt 1)')
            assert 'unsafe' == 'incomplete'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action=action))
    night = await _night(group_hub, monkeypatch, _guide_plan(),
                         guide=lambda who, n: who != _name("1-2"))
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "incomplete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert _shot(night, "1-2") == [], night.shots()
    tries = _gotos(night, "1-2")
    assert len(tries) == 3, tries
    others_first = max(_gotos(night, lb)[0] for lb in ("1-1", "2-2", "2-1"))
    assert tries[1] > others_first, (tries, others_first)
    said = night.said("guiding did not start on 1-2 on 3 consecutive visits: "
                      "no guide star found (attempt 3)")
    assert len(said) == 1, night.lines[-6:]
    assert [r["target_id"] for r in night.stored.set_aside] == ["p01"]


async def test_a_panel_that_guides_on_its_third_try_banks_and_starts_afresh(
        group_hub, group_store, monkeypatch):
    """1-2's guider fails on tries 1 and 2, starts on 3, fails on 4 and 5,
    and starts from 6 on. Its counter is reset by the visit that banks
    frames, so it never reaches three in a row: it is never set aside and
    completes its four L and four R (spec 5.6 step 7: consecutive passes).

    MUTANT "lifetime failures" (the reset of ``failed`` on a visit that
    banks a frame deleted, group_rules.py): the fourth failure is a third.
    RED (observed):
        AssertionError: [{'night': '2026-09-01', 'reason': 'guiding did not
        start on 1-2 on 3 consecutive visits: no guide star found (attempt
        4)', 'step_id': None, 'target_id': 'p01'}]
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="warn"))
    fails = {1, 2, 4, 5}
    counted: dict[int, int] = {}
    box: dict = {}

    def goto(who, n, result):
        # 1-2's failure count as each of its hops begins.
        if who == _name("1-2"):
            counted[n] = box["engine"]._group_runs["m31-mosaic"].failed["p01"]
        return result

    night = Night(group_hub, monkeypatch, goto=goto,
                  guide=lambda who, n: not (who == _name("1-2") and n in fails))
    box["engine"] = night.engine
    try:
        done = await night.run(_guide_plan(panel_kw={"count": 4}))
    finally:
        await night.close()
    assert done, night.trace[-3:]
    stored = session_store.load(night.session_id)
    assert stored.set_aside == [], stored.set_aside
    assert _shot(night, "1-2") == ["L", "R"] * 4
    assert counted.get(3) == 2 and counted.get(4) == 0, (
        f"1-2's count at each hop: {counted}; the visit that banked on the "
        f"third try did not start it afresh")


@pytest.mark.parametrize("action", ["warn", "skip", "abort"])
async def test_a_guider_that_fails_on_every_panel_is_the_rigs_fault(
        group_hub, group_store, monkeypatch, action):
    """No panel's guider starts. After one pass in which all four attempted
    panels failed, no panel's counter has moved and the rig's
    ``guiding_action`` decides, as it does for a single target: abort ends
    the run, skip sets the group aside for tonight, warn shoots the panels
    unguided (spec 5.6 step 7, the owner list's item 1).

    MUTANT "defer even then" (`GroupRun.close_pass` skipping the rig verdict,
    group_rules.py): three passes of hops at a dead guider before every
    panel is set aside, whatever the action. RED on each (observed):
        warn:
            AssertionError: assert [('M31 1-1', ...M31 2-1', ())] == [('M31
            1-1', ..., ('L', 'R'))]
              At index 0 diff: ('M31 1-1', ()) != ('M31 1-1', ('L', 'R'))
        skip:
            AssertionError: assert (12 == 4)
        abort:
            AssertionError: assert 'incomplete' == 'unsafe'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action=action))
    night = await _night(group_hub, monkeypatch, _guide_plan(),
                         guide=lambda who, n: False)
    assert night.done, night.trace[-3:]
    first_pass = _labels(night.visits())[:4]
    assert first_pass == SNAKE, first_pass
    if action == "abort":
        assert night.engine.state.get("end_reason") == "unsafe"
        assert len(night.gotos) == 4 and night.shots() == []
    elif action == "skip":
        assert len(night.gotos) == 4 and night.shots() == []
        assert sorted(r["target_id"] for r in night.stored.set_aside) == [
            "p00", "p01", "p10", "p11"], night.stored.set_aside
    else:
        assert night.visits()[4:8] == [(_name(lb), ("L", "R")) for lb in SNAKE]
        assert night.stored.owed() == 0 and night.stored.set_aside == []


@pytest.mark.parametrize("action", ["warn", "skip", "abort"])
async def test_two_panels_failing_beside_two_that_guide_are_the_panels_fault(
        group_hub, group_store, monkeypatch, action):
    """1-2 and 2-1 never start guiding; 1-1 and 2-2 always do. Two attempted
    panels failed in each pass, but not EVERY attempted panel, so the guider
    is not to blame: ``guiding_action`` is never asked, whatever it says,
    the two failing panels are deferred pass after pass and set aside at
    their third consecutive failure, and the two that guide complete (spec
    5.6 step 7: "at least two panels were attempted in a pass and every one
    of them failed"). What tells the two apart is the hop's start that
    WORKED, which `_visit_panel` hands to the pass rule. Added by the T10
    verifier (#286): nothing tested that wiring.

    MUTANT "guide_started never passed" (`_visit_panel` handing
    ``guide_started=False`` to `GroupRun.visit_outcome`): each pass then
    counts only its two failures, two of two, and reads as the rig's fault.
    RED on each (observed):
        warn (the group goes unguided, so 1-2 and 2-1 are shot unguided):
            AssertionError: ('complete', 'all targets complete')
            assert 'complete' == 'incomplete'
        skip (the whole mosaic is set aside after pass 1):
            AssertionError: ["M31: guiding did not start on 1-2: no guide star
            found (attempt 1); retried on the next pass (counted when the pass
            ...rt on any panel of M31; the mosaic is set aside for tonight: a
            restart tonight does not retry it, the next night does']
            assert not ["M31: guiding did not start on any of the 2 panels
            tried this pass: the guider's fault, not a panel's. No panel's
            fai...rt on any panel of M31; the mosaic is set aside for tonight:
            a restart tonight does not retry it, the next night does']
        abort:
            AssertionError: ('unsafe', 'guiding required but it did not start
            on any panel of M31')
            assert 'unsafe' == 'incomplete'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action=action))
    bad = {_name("1-2"), _name("2-1")}
    night = await _night(group_hub, monkeypatch, _guide_plan(),
                         guide=lambda who, n: who not in bad)
    assert night.done, night.trace[-3:]
    ended = (night.engine.state.get("end_reason"),
             night.engine.state.get("detail"))
    assert ended[0] == "incomplete", ended
    assert not night.said("did not start on any"), night.said("did not start")
    for label in ("1-2", "2-1"):
        assert _shot(night, label) == [], night.shots()
        assert len(_gotos(night, label)) == 3, (label, _gotos(night, label))
    for label in ("1-1", "2-2"):
        assert _shot(night, label) == ["L", "R"] * 3, (ended, label)
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p01", "p10"], night.stored.set_aside


@pytest.mark.parametrize("action", ["warn", "skip", "abort"])
async def test_no_guider_connected_defers_the_pass_then_the_rigs_action_decides(
        group_hub, group_store, monkeypatch, action):
    """The plan asks for guiding and no guider is connected: each member's
    hop is deferred, never escalated on its own (spec 5.6 step 7: "the start
    fails or no guider is connected"), so the first pass visits all four
    panels and shoots nothing. Four attempted, four failed: the rig's fault,
    and ``guiding_action`` decides for the group. Abort ends the run, skip
    sets the whole mosaic aside for tonight, recorded, and warn shoots the
    panels unguided from the second pass on. Added by the T10 verifier
    (#286): only a start that fails was tested.

    MUTANT "no-guider branch does not defer" (the ``PanelDeferred`` in
    `_setup_target`'s not-connected branch deleted): each hop escalates by
    itself, as a single target's does. RED on each (observed):
        warn (the first pass is shot unguided):
            AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')),
            ('M31 2-2', ('L', 'R')), ('M31 2-1', ('L', 'R')), ('M31 1-1',
            ('L', 'R')), ('M31 1-2', ('L', 'R')), ...]
            assert [('M31 1-1', ..., ('L', 'R'))] == [('M31 1-1', ...M31 2-1',
            ())]
              At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ())
        skip (each panel dropped for this run only, nothing recorded):
            AssertionError: []
            assert [] == ['p00', 'p01', 'p10', 'p11']
        abort (the run ends at the first panel):
            AssertionError: ['1-1']
            assert ['1-1'] == ['1-1', '1-2', '2-2', '2-1']
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action=action))
    group_hub.guider.connected = False
    night = await _night(group_hub, monkeypatch, _guide_plan())
    assert night.done, night.trace[-3:]
    first_pass = night.visits()[:4]
    assert _labels(first_pass) == SNAKE, _labels(night.visits())
    assert first_pass == [(_name(lb), ()) for lb in SNAKE], night.visits()
    if action == "abort":
        assert night.engine.state.get("end_reason") == "unsafe"
        assert len(night.gotos) == 4 and night.shots() == []
    elif action == "skip":
        assert len(night.gotos) == 4 and night.shots() == []
        assert sorted(r["target_id"] for r in night.stored.set_aside) == [
            "p00", "p01", "p10", "p11"], night.stored.set_aside
        assert {r["night"] for r in night.stored.set_aside} == {night_key(T0)}
    else:
        assert night.visits()[4:8] == [(_name(lb), ("L", "R")) for lb in SNAKE]
        assert night.stored.owed() == 0 and night.stored.set_aside == []


async def test_a_start_that_fails_at_a_holds_release_counts_as_the_visits_last(
        group_hub, group_store, monkeypatch):
    """A hold's release re-runs the member's setup inside its visit (the
    re-acquisition a cloud hold or a safety pause makes). Here 1-1's guider
    starts at the hop and fails at that second setup: the visit ends in a
    guide-start deferral, 1-1 is retried on the next pass, and the pass rule
    is handed the visit's LAST start, never "one that worked and one that
    failed" for one visit (which `GroupRun.visit_outcome` refuses).

    MUTANT "the start's flag is not reset" (the reset of
    ``_hop_guide_started`` at the top of `_setup_target`'s guiding block
    deleted): the run ends in an error. RED (observed):
        AssertionError: ('error', 'a guide start cannot both succeed and
        fail')
        assert 'error' == 'complete'
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="warn"))
    real_visit = engine_mod.SequenceEngine._run_visit
    released: list[str] = []

    async def visit(self, ti, target, bound):
        if target.name == _name("1-1") and not released:
            released.append(target.name)
            await self._setup_target(ti, target)     # the hold's release
        await real_visit(self, ti, target, bound)

    monkeypatch.setattr(engine_mod.SequenceEngine, "_run_visit", visit)
    night = await _night(group_hub, monkeypatch, _guide_plan(),
                         guide=lambda who, n: not (who == _name("1-1")
                                                   and n == 2))
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "complete", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.visits()[0] == (_name("1-1"), ()), night.visits()[:5]
    assert _shot(night, "1-1") == ["L", "R"] * 3


async def test_a_deferral_at_a_real_cloud_holds_release_ends_the_published_hold(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The same deferral through a REAL cloud hold, on the cloud-hold harness
    (test_cloud_hold_watch's `_Watched`: the real frame loop and
    `_hold_for_clear` on the fake clock, the sky a script). Panel 1-1's
    visit shoots L, the sky shuts before R, and the frame loop's hold holds
    1-1 until the sky clears. The hold's release re-acquires 1-1 through
    `_setup_target`, whose guider start fails there, so the release raises
    `PanelDeferred` out of the hold, past the release's own publish that
    ends the hold, and 1-1 is retried on the next pass. The run goes on to
    1-2, and nothing it publishes from then on may still say the run is
    held for cloud: `_set_state` merges, so a ``hold`` nobody cleared rides
    every later publish of the night (the StopTarget arm of
    `_hold_for_clear` clears it for the same reason).

    A verifier's finding on T10 (#285), fixed in `_visit_panel`: before the
    fix this test failed exactly as the mutant below does.

    MUTANT "a deferral leaves the published state as it was" (the
    ``_set_state(state="running", hold=None, ...)`` in `_visit_panel`'s
    deferral arm deleted): RED (observed):
        AssertionError: 1-2's frames were published as held for cloud:
        [('running', 'clouds', 'G 1-2: L 30s  [1/2]'), ('running', 'clouds',
        'G 1-2: L 30s  [1/2]')]
    """
    from _group_harness import ScriptedGuider
    from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                           TargetGroup)
    from test_cloud_hold_watch import EXP, MAX_HOLD_S, _Watched
    from test_idle_park_hold import _ra_at

    if sim_hub.guider is not None:
        await sim_hub.guider.disconnect()
    guider = ScriptedGuider()
    # Every start is filed under one name here (the harness names no
    # target), so the count is the night's: start 1 is 1-1's hop, start 2
    # the hold's release of 1-1, which fails; every other start works.
    guider.script = lambda who, n: n != 2
    sim_hub.guider = guider
    w = _Watched(sim_hub, temp_store, monkeypatch,
                 horizon_s=EXP + MAX_HOLD_S, clears_at_s=EXP + 300.0)
    panels = [Target(id=f"q0{c}", name=f"G 1-{c + 1}",
                     ra_hours=_ra_at(-3.0, w.t0), dec_deg=40.0 + 0.3 * c,
                     center=False, autofocus_first=False, acquisition="cycle",
                     mosaic_group="g", panel_row=0, panel_col=c,
                     steps=[ExposureStep(id=f"q0{c}-{f}", filter=f,
                                         exposure_s=EXP, gain=100, count=2)
                            for f in ("L", "R")])
              for c in (0, 1)]
    plan = SequencePlan(name="hold", guide=True, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        safety_check=True, recover_guiding=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        targets=panels,
                        groups=[TargetGroup(id="g", name="G",
                                            geometry={"rows": 1, "cols": 2})])
    try:
        await w.night(plan, to_the_horizon=False, timeout=60.0)
    finally:
        await w.close()
    t_h = w.hold_started()
    assert t_h - w.t0 < 2 * EXP, "premise: 1-1's hold began before its R"
    assert guider.attempts.get("", 0) >= 3 and w.clears_at is not None
    released = [m for _lvl, m, src in bus_lines if src == "sequence"
                and m.startswith("G: guiding did not start on 1-1: no guide "
                                 "star found (attempt 2); retried on the next "
                                 "pass")]
    assert len(released) == 1, "premise: the hold's release deferred 1-1"
    later = [(st, hold, d) for _t, st, hold, d in w.states
             if d.startswith("G 1-2: ")]
    assert later, "premise: 1-2 was shot after the deferral"
    assert all(hold is None and st == "running" for st, hold, _d in later), (
        f"1-2's frames were published as held for cloud: {later[:2]}")
    assert any(d == "G: 1-1 deferred to the next pass"
               for _t, _st, _h, d in w.states), w.states[-8:]
    assert w.engine.state.get("end_reason") == "complete", (
        w.engine.state.get("end_reason"), w.engine.state.get("detail"))


# ---------------------------------------------------- completion and modes

def _notify_plan(**kw):
    return grid_plan(instructions=[Instruction(
        id="done-rule", trigger="on_target_complete", action="notify",
        message="a target is complete", level="info")], **kw)


async def test_on_target_complete_fires_once_per_panel_at_its_completion(
        group_hub, monkeypatch):
    """The ``on_target_complete`` rule fires four times, once per panel, each
    at the fake instant that panel's last frame ended, and never after a
    visit that left the panel short (#157, spec 5.1 outcome table, row 1).

    MUTANT "fire every visit" (`_visit_panel` firing the rule after every
    visit, whatever its outcome): RED (observed):
        AssertionError: ([60.0, 120.0, 180.0, 240.0, 300.0, 360.0, ...],
        [540.0, 600.0, 660.0, 720.0])
    """
    night = await _night(group_hub, monkeypatch, _notify_plan())
    assert night.done, night.trace[-3:]
    fired = [t for t, _lvl, m in night.lines if m == "a target is complete"]
    last_frames = sorted(
        max(c["t"] + c["exposure_s"] for c in night.captures
            if c["target"] == _name(lb)) for lb in SNAKE)
    assert fired == last_frames, (
        [night.rel(t) for t in fired], [night.rel(t) for t in last_frames])


async def test_a_single_target_whose_every_frame_is_rejected_is_not_complete(
        group_hub, monkeypatch):
    """#157, the single target: an accepted-mode target whose every frame is
    rejected has each step set aside by its guard, and `_run_steps` returns
    with the target short. Its ``on_target_complete`` rule does not fire.
    The control, the same target with frames accepted, fires it once.

    MUTANT "fire on every return" (the ``_target_complete`` condition on the
    single target's rule in `_schedule_loop` deleted): RED (observed):
        AssertionError: [(1788313779.0, 'warning', 'Solo: L set aside for
        tonight after 2 consecutive rejects — its 2 frame(s) stay owed in
        th...hem owed; a restart tonight does not retry them; the next night
        does'), (1788313809.0, 'info', 'a target is complete')]
        assert ['a target is complete'] == []
          Left contains one more item: 'a target is complete'
    """
    def plan():
        from astrodeck.sequence.models import SequencePlan
        return SequencePlan(
            name="single", guide=False, dither_every=0, autofocus_every=0,
            meridian_flip=False, park_when_done=False,
            warm_cooler_when_done=False, count_mode="accepted", min_stars=5,
            max_consecutive_rejects=2, max_consecutive_rejects_night=0,
            targets=[single("Solo", filters=("L", "R"), count=2)],
            instructions=[Instruction(id="done-rule",
                                      trigger="on_target_complete",
                                      action="notify",
                                      message="a target is complete",
                                      level="info")])

    night = await _night(group_hub, monkeypatch, plan(),
                         stars=lambda who, f: 0)
    assert night.done
    assert night.said("a target is complete") == [], night.lines[-5:]
    control = await _night(group_hub, monkeypatch, plan())
    assert len(control.said("a target is complete")) == 1


async def test_sequential_mode_runs_each_panel_to_completion_in_turn(
        group_hub, monkeypatch):
    """``mode = "sequential"`` (no loop wire): the same machinery, no visit
    bound, so each panel is visited once and shoots all it owes before the
    next (spec 5.1: sequential mode).

    MUTANT "sequential treated as rotate" (`_visit_panel` giving every mode
    the VisitBound): RED (observed):
        AssertionError: assert [('M31 1-1', ...', 'R')), ...] == [('M31 1-1',
        ...', 'L', 'R'))]
          At index 0 diff: ('M31 1-1', ('L', 'R')) != ('M31 1-1', ('L', 'R',
        'L', 'R'))
    """
    plan = grid_plan(group_kw={"mode": "sequential"}, panel_kw={"count": 2})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert night.visits() == [(_name(lb), ("L", "R", "L", "R")) for lb in SNAKE]
    assert night.stored.owed() == 0


# ------------------------------------------------ published state and ETA

async def test_the_group_state_is_published_only_while_a_member_is_active(
        group_hub, monkeypatch):
    """A plain target before the mosaic and one after it. While a panel is
    the active target the state carries ``group``, with ``meridian_wait``
    False; before, after and at the end it carries none. The frames say
    which mosaic and which panel: ``hub.capture`` gets ``mosaic`` and
    ``panel`` for a member's light and neither for the plain targets (U-08).

    ``group``'s key set is not copied here. test_types_mirror_groups.py
    captures the published ``state.group`` on this harness and holds its
    keys to types.ts's ``SequenceGroupState`` both ways round, and
    test_mosaic_spec_claims.py holds the spec's 5.10 list to `_group_state`
    (#318: a hand-written copy here was a second source that nothing tied
    to the engine).

    MUTANT "group never cleared" (the pop of ``group`` in `_set_state`
    deleted): the last panel rides into the plain target's publishes. RED
    (observed):
        AssertionError: {'exposure_s': 30.0, 'extra': {}, 'filter': 'L',
        'group': {'id': 'm31-mosaic', 'meridian_wait': False, 'mode':
        'rotate', 'name': 'M31', ...}, ...}
        assert {'id': 'm31-mosaic', 'meridian_wait': False, 'mode': 'rotate',
        'name': 'M31', ...} is None
    MUTANT "no panel keywords" (`_capture` passing no ``mosaic``/``panel``):
    RED (observed):
        AssertionError: {'exposure_s': 30.0, 'extra': {}, 'filter': 'L',
        'group': {'id': 'm31-mosaic', 'meridian_wait': False, 'mode':
        'rotate', 'name': 'M31', ...}, ...}
        assert {} == {'mosaic': 'M...panel': '1-1'}
          Right contains 2 more items:
          {'mosaic': 'M31', 'panel': '1-1'}
    """
    plan = grid_plan(before=[single("Lead")], after=[single("Tail")],
                     panel_kw={"count": 1})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    order = [t for t, _f in night.shots()]
    assert order == (["Lead"] + [_name(lb) for lb in SNAKE for _ in "LR"]
                     + ["Tail"]), order
    for c in night.captures:
        if c["target"] in ("Lead", "Tail"):
            assert c["group"] is None, c
            assert c["extra"] == {}, c
        else:
            g = c["group"]
            assert g["id"] == "m31-mosaic" and g["name"] == GROUP_NAME, g
            assert g["mode"] == "rotate" and g["meridian_wait"] is False, g
            assert g["panel"] == _label(c["target"]), (g, c["target"])
            assert g["panels_total"] == 4 and g["set_aside"] == [], g
            assert c["extra"] == {"mosaic": GROUP_NAME,
                                  "panel": _label(c["target"])}, c
    assert "group" not in night.states[-1]["keys"], night.states[-1]
    dones = [c["group"]["panels_done"] for c in night.captures
             if c["group"] and c["filter"] == "L"]
    assert dones == [0, 1, 2, 3], dones


@pytest.mark.parametrize("group_kw, panel_kw, lead, first", [
    ({}, {}, 12, 11),
    ({"visit_passes": 2}, {"count": 4}, 8, 7),
    ({"mode": "sequential"}, {}, 4, 3),
], ids=["one-pass", "two-passes", "sequential"])
async def test_the_eta_prices_the_visits_still_to_make(
        group_hub, monkeypatch, group_kw, panel_kw, lead, first):
    """A 2x2 of L and R, count 3, one pass a visit, behind a plain target:
    while the plain target is shot, the hops still to make are the group's
    twelve visits (three rounds a panel), and during 1-1's first visit
    eleven, since that visit's hop is paid (spec 5.10: the hops still to
    make become the visits still to make). At two passes a visit, count 4
    (four rounds, two visits a panel), eight and seven. A sequential group
    runs each panel to completion, one visit each: four and three. (The two
    later cases were added by the T10 verifier, #286.)

    MUTANT "hops = targets remaining" (`_remaining_hops` counting a member
    as one hop, as it counts any target): RED (observed):
        AssertionError: {'Lead': 4, 'M31 1-1': 3, 'M31 1-2': 3, 'M31 2-1': 3,
        ...}
        assert 4 == 12
    MUTANT "eta ignores visit_passes" (`_visits_owed` pricing a rotating
    member's rounds as its visits): RED on two-passes (observed):
        AssertionError: {'Lead': 16, 'M31 1-1': 15, 'M31 1-2': 13, 'M31 2-1':
        9, ...}
        assert 16 == 8
    """
    seen: dict[str, int] = {}
    box: dict = {}

    def hops(rec):
        seen.setdefault(rec["target"], box["engine"]._remaining_hops())

    night = Night(group_hub, monkeypatch)
    box["engine"] = night.engine
    night.on_capture = hops
    try:
        done = await night.run(grid_plan(before=[single("Lead")],
                                         group_kw=group_kw, panel_kw=panel_kw))
    finally:
        await night.close()
    assert done, night.trace[-3:]
    assert seen["Lead"] == lead, seen
    assert seen[_name("1-1")] == first, seen


async def test_a_member_dropped_for_this_run_leaves_its_group_too(
        group_hub, monkeypatch):
    """A member that leaves ``remaining`` by a route that is not a visit (a
    ``skip_target`` rule, a missed start, its frozen window closing; since
    #316 a plain StopTarget from a visit defers the panel instead, see
    test_group_plain_stop_defers.py) is no longer
    live in its group: the published ``set_aside`` names it with its reason,
    in words, so the Monitor and the pass rules see the group as it now is.
    Such a drop lasts this run and is not recorded for the night, as it
    never was.

    MUTANT "the group never hears of it" (the `_group_member_gone` call in
    the skip-drain deleted): RED (observed):
        AssertionError: [{'id': 'm31-mosaic', 'meridian_wait': False, 'mode':
        'rotate', 'name': 'M31', ...}]
          Right contains one more item: {'panel': '2-1', 'reason': 'skipped by
        instruction'}
    """
    plan = grid_plan(instructions=[Instruction(
        id="skip-rule", trigger="on_hfr_above", threshold=1.0, once=True,
        action="skip_target", target_arg=_name("2-1"))])
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.trace[-3:]
    assert _gotos(night, "2-1") == [], night.gotos
    later = [c["group"] for c in night.captures if c["target"] == _name("1-2")]
    assert later and later[0]["set_aside"] == [
        {"panel": "2-1", "reason": "skipped by instruction"}], later[:1]
    assert night.stored.set_aside == [], night.stored.set_aside


# ------------------------------------------------------------ words only

async def test_the_group_drivers_lines_do_not_move_with_the_site(
        group_hub, group_store, monkeypatch):
    """The group driver's lines are words (spec 6.9, 5.10): panel labels,
    counts and causes, never a number the site sets. The same night, a panel
    that never centres deferred and set aside, is run at the fixture site
    and at a site 10 degrees further south: every line the engine logs is
    the same, word for word. The #19 scanner's rule, applied to this
    driver's lines: a number a viewer reads must not move when the site
    does.

    MUTANT "a set-aside line carries the panel's altitude" (`_set_panel_aside`
    adding ``_frame_altitude(...)`` to its warning; the altitudes are the
    fixture sites' own): RED (observed):
        assert ["sequence 'M...M31 2-2', ...] == ["sequence 'M...M31 2-2', ...]
          At index 14 diff: 'M31: centring failed on 2-2 on 3 consecutive
        visits: plate solve failed — used raw GoTo (altitude 68.7); set aside
        for tonight: a restart tonight does not retry it, the next night does'
        != 'M31: centring failed on 2-2 on 3 consecutive visits: plate solve
        failed — used raw GoTo (altitude 65.0); set aside for tonight: a
        restart tonight does not retry it, the next night does'
    """
    from astrodeck.config import Site
    from _group_harness import LAT, LON

    def goto(who, n, result):
        if who == _name("2-2"):
            return {**result, "centered": False, "error_arcmin": None}
        return result

    here = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    group_store.set_site(Site(name="Fixture", latitude=LAT - 10.0,
                              longitude=LON, is_default=False))
    there = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    assert here.done and there.done
    said_here = [m for _t, _l, m in here.lines]
    said_there = [m for _t, _l, m in there.lines]
    assert any("centring failed on 2-2" in m for m in said_here), said_here
    assert said_here == said_there, [
        (a, b) for a, b in zip(said_here, said_there) if a != b][:3]


# ------------------------------------------------------ the visit deadline

async def test_a_visit_ends_at_the_frame_that_would_cross_its_deadline(
        group_hub, monkeypatch):
    """A bound with a ``deadline_ts`` (the flip point or a follower's bound,
    which later slices set) ends the visit at the first frame boundary that
    cannot fit the next frame: 30 s of exposure, the 12 s overhead seed and
    the 30 s flip margin, 72 s, against a deadline 100 s after the visit
    begins. One frame fits, the second would end at 102 s (spec 5.3).

    MUTANT "deadline ignored" (`_run_visit` never asking
    `VisitBound.next_frame_fits`): RED (observed):
        AssertionError: [('M31 1-1', ('L', 'R', 'L', 'R', 'L', 'R')), ('M31
        1-2', ('L', 'R', 'L', 'R', 'L', 'R')), ('M31 2-2', ('L', 'R', 'L',
        'R', 'L', 'R'))]
        assert ('M31 1-1', (...R', 'L', 'R')) == ('M31 1-1', ('L',))
    """
    made: list[VisitBound] = []

    def bound(*, passes, min_s):
        vb = VisitBound(passes=None, deadline_ts=engine_mod.time.time() + 100.0)
        made.append(vb)
        return vb

    monkeypatch.setattr(engine_mod, "VisitBound", bound)
    night = Night(group_hub, monkeypatch)
    try:
        await night.run(grid_plan(), wall_s=10.0)
    finally:
        await night.close()
    assert made, "premise: a visit was bounded"
    first = night.visits()[0]
    assert first == (_name("1-1"), ("L",)), night.visits()[:3]
    assert night.said("the visit ends here; its next frame would not finish "
                      "before the visit's deadline"), night.lines[-5:]
