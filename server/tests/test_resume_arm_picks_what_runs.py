# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Auto-resume re-centres on what the run will shoot, at its angle (#159,
I-13; #189 S2 item 6; mosaic spec 5.9, 3.4, Revision 2 ruling 9).

WHAT WAS WRONG. ``ResumeArm._recover`` ends its ladder with a re-centring
slew, and it slewed to ``next(t for t in plan.targets if not
t.calibration)``: the first light target in the plan, whatever the ledger
said. So:

* a session whose first target was COMPLETE re-centred on it, which the run
  then never shoots, and when that finished target had set below its start
  floor the whole resume refused, retrying every ten minutes, while the
  target the run was going to shoot stood high;
* a mosaic re-centred on panel 1-1 whatever the panel order said, although
  the run starts on the least complete panel (spec 5.2), and on a panel set
  aside tonight, which a same-night resume must not retry (3.4);
* the slew carried no angle, so a resumed night framed at whatever angle the
  rotator was left at: a planned ``rotation_deg`` and a locked angle (ruling
  9) were both dropped.

WHAT IT DOES NOW. The candidates are the light targets that still owe frames
and are not set aside for tonight's night key, in the order the run walks
them (``schedule.schedule_order``: the window that opened first, then plan
order), with each ``plan.groups`` entry's live panels in the order
``panel_order.order_panels`` gives from the ledger, and a target that waits for
a group (``after_group``) left out while that group owes frames. The ladder
tries them in turn: the first that clears its start floor and the slew-limit
gate is re-centred, with its planned angle, else its locked angle, else none.
It refuses only when no candidate is shootable, in the first candidate's
words, which are today's words for a single-target plan. A session with
nothing to shoot tonight (every light target that owes frames set aside, and
no calibration owed) is refused before anything moves. Since #283 the
candidates also pass through the run's gating when the site is set (a closed
window drops a target, a ready one comes first); that is
``test_resume_arm_run_gating.py``'s, and every case here that passes a set
site still holds with it. Since #312 a group whose pier record says it
flipped tonight offers its panels past the meridian first; that is
``test_resume_arm_recentre_after_flip.py``'s, and no case here writes a
pier record, so its order is the panel order.

THE HARNESS. ``_simhub.sim_hub``: the real ``Hub`` on the simulator rig with an
isolated config and a real (synthetic, 40 N 74 W) site, a real
``SequenceEngine`` and a real ``ResumeArm``. ``_recover`` is the code under
test and runs unmodified. The two motion calls it ends in are recorded in
place (``goto_and_center`` as its exact positional and keyword arguments), so
these cases grade the CALL; ``test_the_simulator_rotator_ends_at_the_angle``
lets both run for real on the simulator and grades where the rig ended up.
The slew-limit gate is a double that raises the engine's real ``SlewRefused``
for the targets a case names, because the real gate reads the wall clock
(``test_resume_arm.py`` says so) and these cases need a refusal that does not
depend on when the suite runs. The target's own start floor is NOT doubled:
it is ``_frame_altitude`` against the site at the arm's injected clock, and
each clock reading is searched for, so every "below its floor" premise is
computed and asserted, not assumed.

MUTANTS. Each named mutant was applied to ``astrodeck/sequence/resume_arm.py``
in a private scratch copy of ``server/`` (issue #254), never in the shared
tree, and each failure is quoted verbatim (``--tb=short``) on the case that
caught it. Mutant "first non-calibration target" is today's code: the
unmodified file, run against this file before the change. Each quote is the
failing statement and its ``E`` lines; the location lines are left out,
because their line numbers move as this file does. In the quoted diffs a
target is its coordinates: NGC 604 ``(1.572, 30.7853)``, M13 ``(16.6948,
36.4613)``, and the mosaic's panels 1-1 ``(0.7123, 41.269)``, 1-2 ``(0.7723,
41.269)``, 2-1 ``(0.7123, 40.569)`` and 2-2 ``(0.7723, 40.569)``.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from _site_tracking import numeric_tokens

from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.engine import SlewRefused, _frame_altitude
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup)
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import Session, SessionFrame

#: NGC 604 in M33, the target the rig tried to re-centre on at nine degrees
#: on 2026-09-06 (test_resume_arm.py), and M13, fifteen hours of RA away, so
#: one is low while the other is high.
NGC604 = (1.5720, 30.7853)
M13 = (16.6948, 36.4613)

#: M31's centre, for the mosaic cases. No floor is set on the panels unless a
#: case says so, so where M31 stands does not matter to those cases.
M31 = (0.7123, 41.2690)

FLOOR_DEG = 30.0

#: Where the clock searches start. Any instant would do.
T0 = 1_700_000_000.0

#: The limits refusal's words, today's (``_recover``'s SafetyAbort arm).
LIMITS_WORDS = ("re-centering after restart refused: the target is outside "
                "this rig's configured slew limits (altitude floor, horizon, "
                "no-go wedges, pier side or zenith keep-out); not slewing yet")

#: The refusal for a session with nothing to shoot tonight, in the words
#: #283 widened it to (a closed window or a floor never cleared tonight
#: rules a target out as a set-aside record does). A copy, not an import, so
#: a change to the words is a change this file sees.
NOTHING_TONIGHT = ("none of this session's remaining frames can be captured "
                   "tonight: the remaining targets are set aside for tonight, "
                   "past their observing windows, or never above their minimum "
                   "start altitude; waiting until the next night before slewing")

#: What the recorded goto answers in place of a real re-centre.
_CANNED = {"centered": True, "error_arcmin": 0.2, "attempts": 1,
           "rotation": None}


# ------------------------------------------------------------------ builders

def _step(sid: str, count: int = 3, filt: str = "L") -> ExposureStep:
    return ExposureStep(id=sid, filter=filt, exposure_s=0.05, count=count)


def _target(tid: str, name: str, ra: float, dec: float, *,
            floor: float = 0.0, rotation: float | None = None,
            steps: list[ExposureStep] | None = None, group: str | None = None,
            row: int | None = None, col: int | None = None,
            after: str | None = None, calibration: bool = False,
            start_mode: str = "now") -> Target:
    return Target(id=tid, name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False, calibration=calibration,
                  rotation_deg=rotation, mosaic_group=group, panel_row=row,
                  panel_col=col, after_group=after,
                  schedule=Schedule(min_altitude_deg=floor,
                                    start_mode=start_mode),
                  steps=steps if steps is not None else [_step(f"s-{tid}")])


def _banked(target: Target, n: int, *, ts: float = T0 - 3600.0,
            step: int = 0) -> list[SessionFrame]:
    """``n`` accepted frames on ``target``'s step ``step``, all at ``ts``."""
    sid = target.steps[step].id
    return [SessionFrame(ts=ts, night="n1", target_id=target.id, step_id=sid)
            for _ in range(n)]


def _session(targets: list[Target], *, groups: list[TargetGroup] = (),
             frames: list[SessionFrame] = ()) -> Session:
    plan = SequencePlan(name="picks", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=list(targets), groups=list(groups))
    return Session(id="s-picks", name="picks", status="dormant", plan=plan,
                   frames=list(frames), auto_resume=True)


def _mosaic(*, order: str = "least_complete", pa: float = 12.0,
            rotate: bool = True, floor: float = 0.0, positions: bool = True
            ) -> tuple[TargetGroup, dict[str, Target]]:
    """A 2x2 mosaic group, its members in PLAN order 1-1, 1-2, 2-1, 2-2
    (row by row), which is not the snake order 1-1, 1-2, 2-2, 2-1, so the
    two orders part at the second row. As ``to_plan`` builds one (spec 3.4),
    a rotating group's members carry ``rotation_deg = pa_deg`` and a fixed
    camera's (``rotate=False``) carry none. ``positions=False`` leaves every
    member without its grid position. Keyed by label."""
    group = TargetGroup(id="g-m31", name="M31 mosaic", order=order,
                        rotate=rotate, pa_deg=pa,
                        geometry={"rows": 2, "cols": 2})
    panels = {}
    for r in range(2):
        for c in range(2):
            label = f"{r + 1}-{c + 1}"
            panels[label] = _target(
                f"p-{label}", f"M31 {label}", M31[0] + 0.06 * c,
                M31[1] - 0.7 * r, floor=floor,
                rotation=pa if rotate else None, group=group.id,
                row=r if positions else None, col=c if positions else None)
    return group, panels


def _when(site: dict, *conds: tuple[Target, float, float],
          t0: float = T0) -> float:
    """A clock reading at which every ``(target, lo, hi)`` has
    ``lo <= _frame_altitude(target) <= hi`` at ``site``: the same altitude
    function ``_recover``'s floor check asks. Searched, never hardcoded, so
    the premise holds for the fixture's site whatever it is."""
    for i in range(3 * 24 * 12):
        t = t0 + i * 300.0
        alts = [_frame_altitude(tgt, site, t) for tgt, _lo, _hi in conds]
        if all(a is not None and lo <= a <= hi
               for a, (_t, lo, hi) in zip(alts, conds)):
            return t
    raise AssertionError(f"no clock reading in three days meets {conds}")


# ------------------------------------------------------------------- harness

@pytest.fixture
def rig(sim_hub, monkeypatch):
    """The simulator hub with the ladder's two moves recorded, a real engine
    whose slew-limit gate refuses the target ids in ``refuse``, and focus
    trusted so the ladder goes straight from the solve to the re-centre.

    ``gotos`` holds each ``goto_and_center`` call as ``(args, kwargs)``,
    exactly as made; ``solves`` counts the blind solves; ``limited`` is the
    id of every target the gate was asked about, in order."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    gotos: list[tuple[tuple, dict]] = []
    solves: list[float] = []

    async def goto(*args, **kwargs):
        gotos.append((args, kwargs))
        return dict(_CANNED)

    async def solve(*args, **kwargs):
        solves.append(kwargs.get("exposure_s", 0.0))
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)
    engine = SequenceEngine(sim_hub)
    limited: list[str] = []
    refuse: set[str] = set()

    async def check_slew_limits(target, *, cfg=None, plan=None,
                                projected=True):
        limited.append(target.id)
        if target.id in refuse:
            raise SlewRefused(
                f"slew refused: {target.name} would be below the mount's "
                f"altitude floor by the end of a slew there",
                words="below the mount's altitude floor",
                site_detail=f"target {target.name} altitude 12° below "
                            f"safety floor 20° (az 238°)")

    monkeypatch.setattr(engine, "check_slew_limits", check_slew_limits)
    return SimpleNamespace(
        hub=sim_hub, engine=engine, gotos=gotos, solves=solves,
        limited=limited, refuse=refuse,
        arm=lambda t: ResumeArm(engine, sim_hub, clock=lambda: t))


def _goto(target: Target, **kwargs) -> list[tuple[tuple, dict]]:
    """The one re-centre call expected: ``target``'s coordinates as the two
    positional arguments and exactly ``kwargs``. Compared as a list, so a
    miss shows both calls in the diff."""
    return [((target.ra_hours, target.dec_deg), kwargs)]


# ---------------------------------------------- (1) the target the run shoots

async def test_a_finished_target_below_its_floor_gives_way_to_the_next(rig):
    """Target A is complete and below its start floor, target B owes frames
    and stands high. The run will shoot B, so the ladder re-centres on B and
    passes B's ``rotation_deg``; A is not even asked about.

    RED under mutant "first non-calibration target" (today's code: the
    unmodified file), which re-centres on A and so refuses the whole resume
    on A's floor, observed verbatim:

            assert refusal is None, refusal
        E   AssertionError: NGC 604 is below its start floor; not slewing yet
        E   assert 'NGC 604 is below its start floor; not slewing yet' is None
    """
    a = _target("t-a", "NGC 604", *NGC604, floor=FLOOR_DEG)
    b = _target("t-b", "M13", *M13, floor=FLOOR_DEG, rotation=37.5)
    t = _when(rig.hub.site, (a, 5.0, 20.0), (b, 45.0, 85.0))
    s = _session([a, b], frames=_banked(a, 3))
    assert s.remaining()[a.steps[0].id] == 0, "premise: A is complete"
    assert _frame_altitude(a, rig.hub.site, t) < FLOOR_DEG, (
        "premise: A is below its floor")
    arm = rig.arm(t)

    refusal = await arm._recover(s)

    assert refusal is None, refusal
    assert rig.gotos == _goto(b, rotation_deg=37.5)
    assert arm._recentred is not None and arm._recentred.id == b.id
    assert rig.limited == [b.id], rig.limited


async def test_the_resume_start_hands_the_engine_the_target_it_recentred(
        rig, monkeypatch):
    """The same session through a whole tick: the start that follows the
    ladder hands the engine B as the target the mount is tracking (#202),
    because the ladder left the mount on B. With the first light target, it
    would have handed A, which the mount is not on.

    RED under mutant "first non-calibration target" (today's code), which
    refuses on A's floor and starts nothing, observed verbatim:

            assert arm.hold is None, arm.hold
        E   AssertionError: {'owed': 3, 'reason': 'NGC 604 is below its start floor; not slewing yet', 'retry_at': 1700077100.0, 'session_id': 's-picks', ...}
        E   assert {'owed': 3, 'reason': 'NGC 604 is below its start floor; not slewing yet', 'retry_at': 1700077100.0, 'session_id': 's-picks', ...} is None
        E    +  where {'owed': 3, 'reason': 'NGC 604 is below its start floor; not slewing yet', 'retry_at': 1700077100.0, 'session_id': 's-picks', ...} = <astrodeck.sequence.resume_arm.ResumeArm object at 0x000001B6C9400680>.hold
    """
    from astrodeck.sequence.session import session_store
    a = _target("t-a", "NGC 604", *NGC604, floor=FLOOR_DEG)
    b = _target("t-b", "M13", *M13, floor=FLOOR_DEG, rotation=37.5)
    t = _when(rig.hub.site, (a, 5.0, 20.0), (b, 45.0, 85.0))
    session_store.save(_session([a, b], frames=_banked(a, 3)))
    started: list[dict] = []

    def start(plan, *, session=None, tracking=None, operator=True):
        # ``operator``: ResumeArm passes False (#853, ruling R5). Recorded,
        # with the real default, so a start that drops the keyword reads as
        # an operator's.
        started.append({"session": session.id, "tracking": tracking,
                        "operator": operator})

    monkeypatch.setattr(rig.engine, "start", start)
    arm = rig.arm(t)
    monkeypatch.setattr(arm, "_window_open", lambda s, now: True)

    await arm.tick()

    assert arm.hold is None, arm.hold
    assert rig.gotos == _goto(b, rotation_deg=37.5)
    assert len(started) == 1, started
    assert started[0]["tracking"] is not None
    assert started[0]["tracking"].id == b.id, started
    # SEAM S7 (#853, ruling R5): a ResumeArm start is not an operator's, so
    # the engine keeps tonight's hold and re-centre budgets across it.
    # MUTANT "the keyword dropped" (``operator=False, `` removed from
    # resume_arm.py's ``self.engine.start(...)`` call): RED -
    #     AssertionError: a ResumeArm start read as an operator's
    assert started[0]["operator"] is False, (
        "a ResumeArm start read as an operator's")


async def test_targets_come_in_the_order_the_run_walks_them(rig,
                                                            monkeypatch):
    """The run walks its targets by ``schedule.schedule_order``: the window
    that opened first comes first, whatever the plan order (#159's own
    suggested fix). B is first in the plan and starts "now"; A starts at
    dusk, already past at this clock, so the run takes A first, and so does
    the ladder. Then the fallback: a site the schedule cannot read at all
    (no latitude, no longitude) walks in plan order, B first, as the ladder
    always did, rather than ending the tick.

    RED under mutant "plan order across targets" (``_recover`` passes no
    walk), which re-centres on B, observed verbatim:

            assert rig.gotos == _goto(a)
        E   assert [((16.6948, 36.4613), {})] == [((1.572, 30.7853), {})]
        E
        E     At index 0 diff: ((16.6948, 36.4613), {}) != ((1.572, 30.7853), {})
        E     Use -v to get more diff

    RED under mutant "no walk fallback" (``_walk`` lets the site's
    ``KeyError`` out), on the second part, observed verbatim:

            assert await rig.arm(t)._recover(_session([b, a])) is None
        E   KeyError: 'latitude'

    Since #283 the same site reaches the gating too, which falls back
    the same way (``_gating_state``). RED under mutant "the gating lets
    a site error out" (its ``except`` narrowed to one the schedule
    never raises), on the second part, observed verbatim:

            assert await rig.arm(t)._recover(_session([b, a])) is None
        E   KeyError: 'latitude'
    """
    from astrodeck.config import config_store
    from astrodeck.sequence import schedule
    twilight = config_store.cfg().safety.twilight_deg
    site = rig.hub.site
    lat, lon = site["latitude"], site["longitude"]
    b = _target("t-b", "M13", *M13)
    a = _target("t-a", "NGC 604", *NGC604, start_mode="dusk")
    t = next(T0 + i * 300.0 for i in range(24 * 12)
             if schedule.sun_altitude(lat, lon, T0 + i * 300.0)
             < twilight - 3.0)
    start_a, _stop = schedule.resolve_window(a.schedule, site, twilight, t)
    assert start_a is not None and start_a < t, "premise: A's dusk is past"
    assert schedule.schedule_order([b, a], site, twilight, t) == [a, b], (
        "premise: the run walks A first")

    assert await rig.arm(t)._recover(_session([b, a])) is None
    assert rig.gotos == _goto(a)

    rig.gotos.clear()
    monkeypatch.setattr(type(rig.hub), "site",
                        property(lambda self: {"name": "nowhere"}))
    assert await rig.arm(t)._recover(_session([b, a])) is None
    assert rig.gotos == _goto(b)


# ------------------------------------------------------- (2) the panel order

async def test_a_mosaic_recentres_on_the_least_complete_panel(rig):
    """A 2x2 group with uneven progress: 1-1 two of three, 1-2 complete, 2-1
    two of three, 2-2 one of three. ``order_panels`` puts 2-2 first, so the
    ladder re-centres on 2-2 at the member's rotation, the group's PA.

    RED under mutant "plan order" (a group's live panels kept in plan order,
    not passed through ``order_panels``), which picks 1-1, observed
    verbatim:

            assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7123, 41..._deg': 12.0})] == [((0.7723, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7123, 41.269), {'rotation_deg': 12.0}) != ((0.7723, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic()
    frames = (_banked(p["1-1"], 2) + _banked(p["1-2"], 3)
              + _banked(p["2-1"], 2) + _banked(p["2-2"], 1))
    s = _session(list(p.values()), groups=[group], frames=frames)
    arm = rig.arm(T0)

    assert await arm._recover(s) is None
    assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)


async def test_a_fixed_camera_mosaic_commands_no_angle(rig):
    """The angle is the MEMBER's. A fixed-camera group (``rotate=False``)
    still has a layout angle, ``pa_deg``, which its angle check measures
    against (spec 5.6), but its members carry no ``rotation_deg``: nothing
    is to turn a rotator for it. So the re-centre on its least complete
    panel is today's call, with no angle.

    RED under mutant "the group's PA" (a member's re-centre commands its
    group's ``pa_deg``), observed verbatim:

            assert rig.gotos == _goto(p["2-2"])
        E   AssertionError: assert [((0.7723, 40..._deg': 12.0})] == [((0.7723, 40...9999996), {})]
        E
        E     At index 0 diff: ((0.7723, 40.568999999999996), {'rotation_deg': 12.0}) != ((0.7723, 40.568999999999996), {})
        E     Use -v to get more diff
    """
    group, p = _mosaic(rotate=False)
    assert group.pa_deg == 12.0 and p["2-2"].rotation_deg is None, "premise"
    frames = (_banked(p["1-1"], 2) + _banked(p["1-2"], 3)
              + _banked(p["2-1"], 2) + _banked(p["2-2"], 1))
    s = _session(list(p.values()), groups=[group], frames=frames)

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-2"])


async def test_a_tie_on_completion_goes_to_the_panel_visited_longest_ago(rig):
    """1-1 and 2-1 both one of three, 1-1 visited an hour after 2-1; 1-2 and
    2-2 two of three. The ledger's timestamps reach the order function, so
    2-1, the least recently visited, comes first.

    RED under mutant "no visit times" (the snapshot built without
    ``last_visit_ts``, so the tie falls to the snake, which puts 1-1 first),
    observed verbatim:

            assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7123, 41..._deg': 12.0})] == [((0.7123, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7123, 41.269), {'rotation_deg': 12.0}) != ((0.7123, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic()
    frames = (_banked(p["1-1"], 1, ts=T0 - 1800.0)
              + _banked(p["2-1"], 1, ts=T0 - 5400.0)
              + _banked(p["1-2"], 2, ts=T0 - 7200.0)
              + _banked(p["2-2"], 2, ts=T0 - 9000.0))
    s = _session(list(p.values()), groups=[group], frames=frames)

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)


async def test_a_grid_order_resumes_after_the_panel_visited_last(rig):
    """``order="grid"``: the snake, rotated to start after the panel visited
    last. 1-2 was visited last, so 2-2 is next, although 2-1 has nothing
    banked and would lead under ``least_complete``.

    RED under mutant "policy ignored" (``order_panels`` always called with
    its default policy), which picks 2-1, observed verbatim:

            assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7123, 40..._deg': 12.0})] == [((0.7723, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7123, 40.568999999999996), {'rotation_deg': 12.0}) != ((0.7723, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff

    RED under mutant "no last visit" (the snapshot built without
    ``last_visited``, so the grid starts at the top), which picks 1-1,
    observed verbatim:

            assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7123, 41..._deg': 12.0})] == [((0.7723, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7123, 41.269), {'rotation_deg': 12.0}) != ((0.7723, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic(order="grid")
    frames = (_banked(p["1-1"], 1, ts=T0 - 7200.0)
              + _banked(p["2-2"], 2, ts=T0 - 5400.0)
              + _banked(p["1-2"], 1, ts=T0 - 1800.0))
    s = _session(list(p.values()), groups=[group], frames=frames)

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)


async def test_a_grid_order_resumes_after_a_panel_completed_on_its_visit(rig):
    """The panel visited last may have completed on that visit. It has left
    the live panels, and the grid still resumes after its place, so the
    last visit is looked for among EVERY member (``_group_order``). Snake
    order is 1-1, 1-2, 2-2, 2-1. 2-2 was visited last and is complete, so
    2-1, the panel after it, is next; the live panel visited last is 2-1
    itself, and resuming after it would wrap to 1-1.

    RED under mutant "last visit among live panels only" (``placed`` built
    from ``live``, not ``members``), which picks 1-1, observed verbatim:

            assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7123, 41..._deg': 12.0})] == [((0.7123, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7123, 41.269), {'rotation_deg': 12.0}) != ((0.7123, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic(order="grid")
    frames = (_banked(p["1-2"], 1, ts=T0 - 10800.0)
              + _banked(p["2-1"], 1, ts=T0 - 7200.0)
              + _banked(p["2-2"], 3, ts=T0 - 3600.0))
    s = _session(list(p.values()), groups=[group], frames=frames)
    assert s.remaining()[p["2-2"].steps[0].id] == 0, (
        "premise: 2-2 completed on the last visit")

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)


async def test_a_group_with_no_panel_positions_resumes_in_plan_order(
        rig, bus_lines):
    """Members with no ``panel_row``/``panel_col`` cannot be ordered: the order
    function raises for a panel with no grid position. The ladder does not
    let that end the tick (``_run`` would log "resume-arm tick failed" and
    come back in a minute, forever); it takes the live panels in plan order
    and says so in a warning.

    RED under mutant "no fallback" (the ``ValueError`` from ``order_panels``
    propagates), observed verbatim:

            assert await rig.arm(T0)._recover(s) is None
        E   ValueError: panel row must be an integer, not None
    """
    group, p = _mosaic(positions=False)
    frames = _banked(p["1-1"], 3) + _banked(p["1-2"], 1)
    s = _session(list(p.values()), groups=[group], frames=frames)

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["1-2"], rotation_deg=12.0)
    warned = [m for lv, m, _s in bus_lines
              if lv == "warning" and "panel order" in m]
    assert len(warned) == 1, bus_lines
    assert "M31 mosaic" in warned[0] and "plan order" in warned[0], warned


async def test_a_mosaic_group_with_no_group_entry_keeps_plan_order(rig):
    """A ``mosaic_group`` that names no ``plan.groups`` entry is a Plan-UI
    mosaic, which keeps today's panel-first behaviour (spec 3.4): its panels
    are ordinary targets in plan order. With 1-1 complete, the first that
    owes frames is 1-2, although 2-2 is the least complete.

    RED under mutant "every mosaic_group is ordered" (members found by
    ``mosaic_group`` alone, with a default group for an unknown id), which
    picks 2-2, observed verbatim:

            assert rig.gotos == _goto(p["1-2"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7723, 40..._deg': 12.0})] == [((0.7723, 41..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7723, 40.568999999999996), {'rotation_deg': 12.0}) != ((0.7723, 41.269), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    _group, p = _mosaic()
    frames = (_banked(p["1-1"], 3) + _banked(p["1-2"], 2)
              + _banked(p["2-1"], 2) + _banked(p["2-2"], 1))
    s = _session(list(p.values()), groups=[], frames=frames)

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["1-2"], rotation_deg=12.0)


async def test_a_group_stands_at_its_first_members_place_in_the_plan(rig):
    """Between a group and the plan's other targets the order is the plan's:
    the group stands where its first member stands. Y listed before the
    panels is chosen before any of them; listed after them, the least
    complete panel, 2-2, is.

    RED under mutant "groups first" (every group's panels ahead of the
    plan's other targets), which picks 2-2 over Y, observed verbatim:

            assert rig.gotos == _goto(y)
        E   AssertionError: assert [((0.7723, 40..._deg': 12.0})] == [((16.6948, 36.4613), {})]
        E
        E     At index 0 diff: ((0.7723, 40.568999999999996), {'rotation_deg': 12.0}) != ((16.6948, 36.4613), {})
        E     Use -v to get more diff

    RED under mutant "groups last", which picks Y over the panels, observed
    verbatim:

            assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)
        E   AssertionError: assert [((16.6948, 36.4613), {})] == [((0.7723, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((16.6948, 36.4613), {}) != ((0.7723, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic()
    y = _target("t-y", "M13", *M13)
    frames = (_banked(p["1-1"], 2) + _banked(p["1-2"], 2)
              + _banked(p["2-1"], 2) + _banked(p["2-2"], 1))

    s = _session([y, *p.values()], groups=[group], frames=frames)
    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(y)

    rig.gotos.clear()
    s = _session([*p.values(), y], groups=[group], frames=frames)
    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)


# ----------------------------------------------------- (3) set aside tonight

async def test_a_target_set_aside_tonight_is_passed_over_until_the_next_night(
        rig):
    """A is set aside for tonight's night key, so the ladder re-centres on B.
    A day later the record is another night's and A, first in the plan and
    still owing frames, is chosen again.

    RED under mutant "ignore set_aside" (no record read), which re-centres
    on A tonight, observed verbatim:

            assert rig.gotos == _goto(b)
        E   assert [((1.572, 30.7853), {})] == [((16.6948, 36.4613), {})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {}) != ((16.6948, 36.4613), {})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604)
    b = _target("t-b", "M13", *M13)
    tonight = T0
    s = _session([a, b])
    s.note_set_aside(a.id, "centring failed on 3 passes",
                     night=night_key(tonight))

    assert await rig.arm(tonight)._recover(s) is None
    assert rig.gotos == _goto(b)

    rig.gotos.clear()
    tomorrow = tonight + 86400.0
    assert night_key(tomorrow) != night_key(tonight), "premise: a new night"
    assert await rig.arm(tomorrow)._recover(s) is None
    assert rig.gotos == _goto(a)


async def test_a_panel_set_aside_tonight_gives_way_to_the_next_panel(rig):
    """2-2 is the least complete panel and is set aside tonight, so the
    ladder takes the next in the order, 2-1. The next night it takes 2-2.

    RED under mutant "ignore set_aside", which re-centres on 2-2 tonight,
    observed verbatim:

            assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)
        E   AssertionError: assert [((0.7723, 40..._deg': 12.0})] == [((0.7123, 40..._deg': 12.0})]
        E
        E     At index 0 diff: ((0.7723, 40.568999999999996), {'rotation_deg': 12.0}) != ((0.7123, 40.568999999999996), {'rotation_deg': 12.0})
        E     Use -v to get more diff
    """
    group, p = _mosaic()
    frames = (_banked(p["1-1"], 2, ts=T0 - 7200.0)
              + _banked(p["1-2"], 3, ts=T0 - 6000.0)
              + _banked(p["2-1"], 2, ts=T0 - 9000.0)
              + _banked(p["2-2"], 1, ts=T0 - 3600.0))
    s = _session(list(p.values()), groups=[group], frames=frames)
    s.note_set_aside(p["2-2"].id, "rejected every frame for 3 visits",
                     night=night_key(T0))

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(p["2-1"], rotation_deg=12.0)

    rig.gotos.clear()
    assert await rig.arm(T0 + 86400.0)._recover(s) is None
    assert rig.gotos == _goto(p["2-2"], rotation_deg=12.0)


async def test_a_target_whose_every_owed_step_is_set_aside_is_passed_over(
        rig):
    """The reject guard sets STEPS aside (``step_id`` set). A with both of its
    owed steps set aside tonight has nothing to shoot tonight, so the ladder
    takes B. The control: with only one of A's steps set aside, A still owes
    the other one tonight, and it is chosen.

    RED under mutant "whole-target records only" (a record with a
    ``step_id`` never sets its target aside), which re-centres on A,
    observed verbatim:

            assert rig.gotos == _goto(b)
        E   assert [((1.572, 30.7853), {})] == [((16.6948, 36.4613), {})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {}) != ((16.6948, 36.4613), {})
        E     Use -v to get more diff

    RED under mutant "any step record sets the target aside", which
    re-centres on B in the control, observed verbatim:

            assert rig.gotos == _goto(a), (
        E   AssertionError: control: A still owes R tonight: [((16.6948, 36.4613), {})]
        E   assert [((16.6948, 36.4613), {})] == [((1.572, 30.7853), {})]
        E
        E     At index 0 diff: ((16.6948, 36.4613), {}) != ((1.572, 30.7853), {})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604,
                steps=[_step("s-a-L"), _step("s-a-R", filt="R")])
    b = _target("t-b", "M13", *M13)
    s = _session([a, b])
    s.note_set_aside(a.id, "rejected 10 in a row", night=night_key(T0),
                     step_id="s-a-L")

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(a), (
        f"control: A still owes R tonight: {rig.gotos}")

    rig.gotos.clear()
    s.note_set_aside(a.id, "rejected 10 in a row", night=night_key(T0),
                     step_id="s-a-R")
    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(b)


async def test_nothing_to_shoot_tonight_is_refused_before_anything_moves(
        rig):
    """Every light target that owes frames is set aside tonight, and no
    calibration is owed: the run would shoot nothing, end, and leave the
    session dormant and armed for the next tick to start again. So the ladder
    refuses, in words, before the solve. Two controls, each solved and never
    slewed: with a dark still owed the run has work tonight, and a session
    that owes nothing at all is the run's to complete.

    RED under mutant "start anyway" (the nothing-tonight refusal removed),
    which solves and returns None, observed verbatim (this and the next
    two re-run on 2026-09-25 when #283 widened the words):

            assert refusal == NOTHING_TONIGHT, refusal
        E   AssertionError: None
        E   assert None == 'nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night'

    RED under mutant "a dark owed still refuses" (calibration owed not
    asked), on the first control, observed verbatim:

            assert await rig.arm(T0)._recover(s2) is None
        E   AssertionError: assert 'nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night' is None

    RED under mutant "owing light not required" (the refusal asks only that
    no calibration is owed), on the second control, observed verbatim:

            assert await rig.arm(T0)._recover(s3) is None
        E   AssertionError: assert 'nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night' is None
    """
    a = _target("t-a", "NGC 604", *NGC604)
    s = _session([a])
    s.note_set_aside(a.id, "centring failed on 3 passes",
                     night=night_key(T0))

    refusal = await rig.arm(T0)._recover(s)

    assert refusal == NOTHING_TONIGHT, refusal
    assert numeric_tokens(refusal) == numeric_tokens(""), refusal
    assert rig.solves == [] and rig.gotos == [], (rig.solves, rig.gotos)

    dark = _target("t-dark", "darks", 0.0, 0.0, calibration=True,
                   steps=[ExposureStep(id="s-dark", exposure_s=0.05, count=2,
                                       frame_type="Dark")])
    s2 = _session([a, dark])
    s2.note_set_aside(a.id, "centring failed on 3 passes",
                      night=night_key(T0))
    assert await rig.arm(T0)._recover(s2) is None
    assert rig.gotos == [], rig.gotos
    assert len(rig.solves) == 1, rig.solves

    s3 = _session([a], frames=_banked(a, 3))
    assert s3.owed() == 0, "premise: the session owes nothing"
    assert await rig.arm(T0)._recover(s3) is None
    assert rig.gotos == [], rig.gotos
    assert len(rig.solves) == 2, rig.solves


async def test_a_target_that_waits_for_a_live_group_is_not_recentred(rig):
    """F waits for the mosaic (``after_group``). While the group owes frames
    F is not what the run shoots, so when every panel is below its floor the
    ladder refuses on the first panel's floor rather than slewing to F. Once
    the group is complete, F is chosen. And with every panel set aside
    tonight F is skipped with the group (spec 1.6), which leaves nothing to
    shoot tonight.

    RED under mutant "ignore after_group", which re-centres on F while the
    group still owes frames, observed verbatim:

            assert refusal is not None and "below its start floor" in refusal, (
        E   AssertionError: (None, [((16.6948, 36.4613), {})])
        E   assert (None is not None)

    RED under mutant "a set-aside group releases its followers" (only the
    group's live panels hold F back), which re-centres on F on the third
    part, observed verbatim (re-run on 2026-09-25 when #283 widened the
    words):

            assert refusal == NOTHING_TONIGHT, (refusal, rig.gotos)
        E   AssertionError: (None, [((16.6948, 36.4613), {})])
        E   assert None == 'nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night'
    """
    group, p = _mosaic(floor=FLOOR_DEG)
    f = _target("t-f", "M13", *M13, after=group.id)
    t = _when(rig.hub.site, (p["1-1"], 5.0, 20.0), (p["2-2"], 5.0, 20.0),
              (f, 45.0, 85.0))
    s = _session([*p.values(), f], groups=[group])

    refusal = await rig.arm(t)._recover(s)

    assert refusal is not None and "below its start floor" in refusal, (
        refusal, rig.gotos)
    assert rig.gotos == [], rig.gotos

    done = _session([*p.values(), f], groups=[group],
                    frames=[fr for m in p.values() for fr in _banked(m, 3)])
    assert await rig.arm(t)._recover(done) is None
    assert rig.gotos == _goto(f)

    rig.gotos.clear()
    aside = _session([*p.values(), f], groups=[group])
    for m in p.values():
        aside.note_set_aside(m.id, "a full pass took no exposures",
                             night=night_key(t))
    refusal = await rig.arm(t)._recover(aside)
    assert refusal == NOTHING_TONIGHT, (refusal, rig.gotos)
    assert rig.gotos == [], rig.gotos


# ------------------------------------------------ (4) the angle it commands

async def test_an_unframed_target_is_recentred_at_its_locked_angle(rig):
    """Ruling 9, the resume half: an unframed target (no ``rotation_deg``)
    whose first solve locked its angle is re-centred at the locked PA.

    RED under mutant "no lock" (only the planned ``rotation_deg`` is
    passed), which leaves the resumed night at whatever angle the rotator
    was left at, observed verbatim:

            assert rig.gotos == _goto(a, rotation_deg=41.25)
        E   AssertionError: assert [((1.572, 30.7853), {})] == [((1.572, 30....deg': 41.25})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {}) != ((1.572, 30.7853), {'rotation_deg': 41.25})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604)
    s = _session([a])
    s.lock_angle(a.id, 41.25, solved_at=T0 - 86400.0, exposed_at=None,
                 source="first centring solve")

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(a, rotation_deg=41.25)


async def test_no_lock_and_no_planned_angle_makes_todays_call(rig):
    """No planned angle and no lock: the re-centre call is exactly today's,
    two positional arguments and no keyword at all.

    RED under mutant "always pass the angle" (``rotation_deg=`` passed even
    when it is None), observed verbatim:

            assert rig.gotos == [((a.ra_hours, a.dec_deg), {})], rig.gotos
        E   AssertionError: [((1.572, 30.7853), {'rotation_deg': None})]
        E   assert [((1.572, 30...._deg': None})] == [((1.572, 30.7853), {})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {'rotation_deg': None}) != ((1.572, 30.7853), {})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604)
    s = _session([a])

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == [((a.ra_hours, a.dec_deg), {})], rig.gotos


async def test_a_planned_angle_beats_a_lock(rig):
    """A framed target carries its angle (ruling 9), and a lock under its id
    does not move it: the planned ``rotation_deg`` is commanded.

    RED under mutant "lock beats plan" (the lock read first), observed
    verbatim:

            assert rig.gotos == _goto(a, rotation_deg=20.0)
        E   AssertionError: assert [((1.572, 30...._deg': 99.0})] == [((1.572, 30...._deg': 20.0})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {'rotation_deg': 99.0}) != ((1.572, 30.7853), {'rotation_deg': 20.0})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604, rotation=20.0)
    s = _session([a])
    s.lock_angle(a.id, 99.0, solved_at=T0 - 86400.0, exposed_at=None,
                 source="first centring solve")

    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(a, rotation_deg=20.0)


async def test_pa_zero_is_an_angle(rig):
    """PA 0, north up, is a real angle, locked or planned.

    RED under mutant "truthy commanded PA" (the re-centre's ``if rotation is
    None`` made ``if not rotation``), on the locked 0, observed verbatim:

            assert rig.gotos == _goto(a, rotation_deg=0.0)
        E   AssertionError: assert [((1.572, 30.7853), {})] == [((1.572, 30....n_deg': 0.0})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {}) != ((1.572, 30.7853), {'rotation_deg': 0.0})
        E     Use -v to get more diff

    RED under mutant "truthy planned PA" (``commanded_rotation``'s ``if
    target.rotation_deg is not None`` made ``if target.rotation_deg``), on
    the planned 0, observed verbatim:

            assert rig.gotos == _goto(b, rotation_deg=0.0)
        E   AssertionError: assert [((1.572, 30.7853), {})] == [((1.572, 30....n_deg': 0.0})]
        E
        E     At index 0 diff: ((1.572, 30.7853), {}) != ((1.572, 30.7853), {'rotation_deg': 0.0})
        E     Use -v to get more diff
    """
    a = _target("t-a", "NGC 604", *NGC604)
    s = _session([a])
    s.lock_angle(a.id, 0.0, solved_at=T0 - 86400.0, exposed_at=None,
                 source="first centring solve")
    assert await rig.arm(T0)._recover(s) is None
    assert rig.gotos == _goto(a, rotation_deg=0.0)

    rig.gotos.clear()
    b = _target("t-b", "NGC 604", *NGC604, rotation=0.0)
    assert await rig.arm(T0)._recover(_session([b])) is None
    assert rig.gotos == _goto(b, rotation_deg=0.0)


@pytest.mark.parametrize("pa", [float("nan"), float("inf"), "41.25", True,
                                None])
def test_a_lock_that_is_not_a_finite_angle_commands_nothing(pa):
    """``lock_angle`` refuses a non-finite angle, but the session is a JSON
    file, and Python's JSON reads NaN and Infinity. A lock that is not a
    finite number of degrees commands no angle rather than handing the
    rotate loop a NaN, a string or a bool.

    RED under mutant "trust the lock's value" (``float(lock["pa_deg"])``
    unchecked), every case, observed verbatim (the statement and the first
    ``E`` line of each; the rest is the session's repr):

        [nan]
            assert commanded_rotation(s, a) is None
        E   AssertionError: assert nan is None
        [inf]
            assert commanded_rotation(s, a) is None
        E   AssertionError: assert inf is None
        [41.25]
            assert commanded_rotation(s, a) is None
        E   AssertionError: assert 41.25 is None
        [True]
            assert commanded_rotation(s, a) is None
        E   AssertionError: assert 1.0 is None
        [None]
            assert commanded_rotation(s, a) is None
        E   TypeError: float() argument must be a string or a real number, not 'NoneType'
    """
    from astrodeck.sequence.resume_arm import commanded_rotation
    a = _target("t-a", "NGC 604", *NGC604)
    s = _session([a])
    s.locked_angles[a.id] = {"pa_deg": pa, "solved_at": T0,
                             "exposed_at": None, "source": "hand edit"}
    assert commanded_rotation(s, a) is None


# ------------------------------------------------ (5) when it still refuses

async def test_a_candidate_below_its_floor_gives_way_to_one_above_it(rig):
    """A and B both owe frames. A, first, is below its floor, so the ladder
    goes on to B, which clears it.

    RED under mutant "the first refusal ends the ladder" (a floor or limits
    refusal of any candidate returned at once), observed verbatim:

            assert await rig.arm(t)._recover(_session([a, b])) is None
        E   AssertionError: assert 'NGC 604 is below its start floor; not slewing yet' is None
    """
    a = _target("t-a", "NGC 604", *NGC604, floor=FLOOR_DEG)
    b = _target("t-b", "M13", *M13, floor=FLOOR_DEG)
    t = _when(rig.hub.site, (a, 5.0, 20.0), (b, 45.0, 85.0))

    assert await rig.arm(t)._recover(_session([a, b])) is None
    assert rig.gotos == _goto(b)
    assert rig.limited == [b.id], rig.limited


async def test_a_candidate_the_limits_refuse_gives_way_to_the_next(rig):
    """The slew-limit gate refuses A, so the ladder goes on to B.

    RED under mutant "the first refusal ends the ladder", observed verbatim:

            assert await rig.arm(T0)._recover(_session([a, b])) is None
        E   assert "re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet" is None
    """
    a = _target("t-a", "NGC 604", *NGC604)
    b = _target("t-b", "M13", *M13)
    rig.refuse.add(a.id)

    assert await rig.arm(T0)._recover(_session([a, b])) is None
    assert rig.gotos == _goto(b)
    assert rig.limited == [a.id, b.id], rig.limited


async def test_with_no_shootable_candidate_it_refuses_in_the_first_ones_words(
        rig):
    """Two refusals in turn. First A is refused by the limits and B is below
    its floor: the refusal is A's, in today's site-free words, with A's
    numbers in ``site_detail``. Then both are below their floors: A's floor
    words, with A's numbers. Nothing moves either time.

    RED under mutant "the last refusal is reported" (each refusal
    overwrites the one before), observed verbatim:

            assert refusal == LIMITS_WORDS, refusal
        E   AssertionError: M13 is below its start floor; not slewing yet
        E   assert 'M13 is below...t slewing yet' == 're-centering...t slewing yet'
        E
        E     - re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet
        E     + M13 is below its start floor; not slewing yet
    """
    a = _target("t-a", "NGC 604", *NGC604, floor=FLOOR_DEG)
    b = _target("t-b", "M13", *M13, floor=FLOOR_DEG)
    t = _when(rig.hub.site, (a, 35.0, 60.0), (b, 5.0, 20.0))
    rig.refuse.add(a.id)
    arm = rig.arm(t)

    refusal = await arm._recover(_session([a, b]))

    assert refusal == LIMITS_WORDS, refusal
    assert numeric_tokens(refusal) == numeric_tokens(""), refusal
    assert "NGC 604" in (arm._refusal_site_detail or ""), (
        arm._refusal_site_detail)
    assert rig.gotos == [], rig.gotos

    rig.refuse.clear()
    t2 = _when(rig.hub.site, (a, 5.0, 20.0), (b, 5.0, 20.0))
    arm2 = rig.arm(t2)
    refusal = await arm2._recover(_session([a, b]))

    assert refusal == "NGC 604 is below its start floor; not slewing yet", (
        refusal)
    assert numeric_tokens(refusal) == numeric_tokens("NGC 604"), refusal
    detail = arm2._refusal_site_detail or ""
    assert detail.startswith("NGC 604 is at "), detail
    assert "below its 30 deg start floor" in detail, detail
    assert rig.gotos == [], rig.gotos


# -------------------------------------------------- the rig, not the call

async def test_the_simulator_rotator_ends_at_the_angle(sim_hub, monkeypatch):
    """No spy on the moves: the real blind solve and the real
    ``goto_and_center`` on the simulator. A is complete, so the mount ends on
    B and the simulated rotator's sky angle ends at B's 37.5 degrees (mod
    180, within the 1 degree ``test_goto_rotation.py`` grants), and the goto's
    own result says the rotator was turned. Only the slew-limit gate (the
    wall clock, see the header) and the focus verdict are held still, and
    solar avoidance is off so the time of year cannot refuse the slew.

    RED under mutant "first non-calibration target" (today's code), which
    re-centres on A with no angle and leaves the rotator where it was,
    observed verbatim:

            assert results[0]["rotation"] is not None, results[0]
        E   AssertionError: {'attempts': 1, 'centered': True, 'error_arcmin': 0.16611088694831652, 'rotation': None}
        E   assert None is not None
    """
    from astrodeck.config import config_store
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    monkeypatch.setattr(config_store.cfg().safety, "solar_avoidance", False)
    engine = SequenceEngine(sim_hub)

    async def limits_pass(target, *, cfg=None, plan=None, projected=True):
        return None

    monkeypatch.setattr(engine, "check_slew_limits", limits_pass)
    results: list[dict] = []
    real_goto = sim_hub.goto_and_center

    async def goto(*args, **kwargs):
        result = await real_goto(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    sim = sim_hub.sim_rig
    sim.rotator_pa_offset_deg = 20.0
    sim.rotator_mech_deg = 100.0
    a = _target("t-a", "NGC 604", *NGC604)
    b = _target("t-b", "M13", *M13, rotation=37.5)
    s = _session([a, b], frames=_banked(a, 3))
    arm = ResumeArm(engine, sim_hub, clock=lambda: T0)

    refusal = await arm._recover(s)

    assert refusal is None, refusal
    assert len(results) == 1, results
    assert results[0]["rotation"] is not None, results[0]
    assert abs(sim.ra_hours - b.ra_hours) * 15.0 < 0.5, sim.ra_hours
    assert abs(sim.dec_deg - b.dec_deg) < 0.5, sim.dec_deg
    sky = (sim.rotator_mech_deg + sim.rotator_pa_offset_deg) % 360.0
    off = abs((sky - 37.5 + 90.0) % 180.0 - 90.0)
    assert off <= 1.0, (sky, results[0])
    assert math.isfinite(sky)
