"""Auto-resume's re-centre honours the run's gating (#283, #159 follow-up).

WHAT WAS WRONG. ``recentre_candidates`` listed what the run could still shoot
tonight from the ledger alone: owing, not set aside, not waiting on a group
that owes. The run asks one more question of every target before it shoots
it, ``schedule.gating_status``: it drops a target whose window has closed or
that never clears its start floor tonight, and passes over one whose window
has not opened, or that a moon or hour-angle constraint holds, while any
other target is ready. The ladder asked none of it, so:

* a session whose only owing target's window had closed, beside a complete
  target whose window was still open, passed ``window_open`` (ANY target's
  window), re-centred on the closed target and started. The run dropped it,
  skipped the complete one, and ended with frames still owed, dormant and
  armed, and the next tick a minute later did the same again: a blind solve
  and a slew every minute until the window or the night closed;
* a target waiting on the clock or a constraint was re-centred on while the
  run was going to shoot a ready one first: a wasted slew.

WHAT IT DOES NOW. When the site is set (``site_gate.site_is_set``, #121: an
unset site must not strand a resume), each live candidate's
``gating_status`` is asked at the ladder's clock. ``window_closed`` and
``never_rises`` candidates are dropped, as the run drops them, and ready
candidates are tried before waiting ones, as the run takes the first ready
target in its order. With everything the session owes dropped,
``nothing_to_shoot_tonight`` refuses before anything moves, in words with no
numbers (#233).

THE HARNESS. ``_simhub.sim_hub``: the real ``Hub`` on the simulator rig with a
real synthetic site (40 N 74 W, not anybody's rig), a real ``SequenceEngine``
and a real ``ResumeArm``, whose ``_recover`` and ``tick`` run unmodified. The
blind solve and the re-centring goto are recorded in place, the slew-limit
gate passes (the real one reads the wall clock), focus is trusted, and
``engine.start`` records its calls rather than running a night. Every gating
premise is computed with ``schedule.gating_status`` at the case's clock and
asserted, never assumed, and every clock reading is searched for.

MUTANTS. Each named mutant was applied to ``astrodeck/sequence/resume_arm.py``
in a private scratch copy of ``server/`` (issue #254), never in the shared
tree, and each failure is quoted verbatim (``--tb=short``, ``E`` lines only).
Mutant "no gating" is ``recentre_candidates`` with its gating branch removed:
every live candidate kept, in walk order, which is the code before #283.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from _site_tracking import numeric_tokens

from astrodeck.config import config_store
from astrodeck.sequence import SequenceEngine, schedule
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup)
from astrodeck.sequence.resume_arm import (CHECK_INTERVAL_S, NOTHING_TONIGHT,
                                           RETRY_INTERVAL_S, ResumeArm,
                                           window_open)
from astrodeck.sequence.session import Session, SessionFrame, session_store

#: M13 and NGC 604 again (test_resume_arm_picks_what_runs.py), and a southern
#: target that never clears a high floor from a northern site.
M13 = (16.6948, 36.4613)
NGC604 = (1.5720, 30.7853)
SOUTH = (19.0, -30.0)
M31 = (0.7123, 41.2690)

#: Where the clock searches start. Any instant would do.
T0 = 1_700_000_000.0

TWILIGHT = -12.0


# ------------------------------------------------------------------ builders

def _hhmm(ts: float) -> str:
    """Local ``HH:MM`` at ``ts``, the form ``Schedule.start_time`` and
    ``stop_time`` take, which ``resolve_window`` places at the occurrence
    nearest the clock."""
    return time.strftime("%H:%M", time.localtime(ts))


def _target(tid: str, name: str, ra: float, dec: float, *,
            floor: float = 0.0, start_mode: str = "now",
            start_time: str | None = None, stop_mode: str = "none",
            stop_time: str | None = None, max_ha: float = 0.0,
            group: str | None = None, row: int | None = None,
            col: int | None = None) -> Target:
    return Target(id=tid, name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False, mosaic_group=group, panel_row=row,
                  panel_col=col,
                  schedule=Schedule(min_altitude_deg=floor,
                                    start_mode=start_mode,
                                    start_time=start_time,
                                    stop_mode=stop_mode, stop_time=stop_time,
                                    max_hour_angle_h=max_ha),
                  steps=[ExposureStep(id=f"s-{tid}", filter="L",
                                      exposure_s=0.05, count=3)])


def _banked(target: Target, n: int) -> list[SessionFrame]:
    return [SessionFrame(ts=T0 - 3600.0, night="n1", target_id=target.id,
                         step_id=target.steps[0].id) for _ in range(n)]


def _session(targets, *, groups=(), frames=()) -> Session:
    plan = SequencePlan(name="gating", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=list(targets), groups=list(groups))
    return Session(id="s-gating", name="gating", status="dormant", plan=plan,
                   frames=list(frames), auto_resume=True)


def _state(target: Target, site: dict, t: float) -> dict:
    return schedule.gating_status(target, site, TWILIGHT, t)


def _dark_clock(site: dict) -> float:
    """A clock reading well inside the night at ``site``: dark now and for
    the next three hours, searched, so the premise holds whatever the
    fixture's site is."""
    for i in range(3 * 24 * 12):
        t = T0 + i * 300.0
        if all(schedule.dark_enough(site, TWILIGHT, t + h * 3600.0)
               for h in range(4)):
            return t
    raise AssertionError("no dark clock reading in three days")


# ------------------------------------------------------------------- harness

@pytest.fixture
def rig(sim_hub, monkeypatch):
    """The simulator hub with the ladder's two moves and the engine's start
    recorded, the slew-limit gate passing and focus trusted.

    ``gotos`` holds each re-centre's coordinates, ``solves`` counts the
    blind solves, ``starts`` the ``engine.start`` calls."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    monkeypatch.setattr(config_store.cfg().safety, "twilight_deg", TWILIGHT)
    gotos: list[tuple[float, float]] = []
    solves: list[float] = []
    starts: list[str] = []

    async def goto(ra, dec, **kwargs):
        gotos.append((ra, dec))
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    async def solve(*args, **kwargs):
        solves.append(kwargs.get("exposure_s", 0.0))
        return {}

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)
    engine = SequenceEngine(sim_hub)

    async def limits_pass(target, *, cfg=None, plan=None, projected=True):
        return None

    def start(plan, *, session=None, tracking=None):
        starts.append(session.id)

    monkeypatch.setattr(engine, "check_slew_limits", limits_pass)
    monkeypatch.setattr(engine, "start", start)
    clock = {"t": T0}
    return SimpleNamespace(
        hub=sim_hub, engine=engine, gotos=gotos, solves=solves,
        starts=starts, clock=clock,
        arm=ResumeArm(engine, sim_hub, clock=lambda: clock["t"]))


def _at(target: Target) -> tuple[float, float]:
    return (target.ra_hours, target.dec_deg)


# ------------------------------------------- (1) a closed window, not a loop

async def test_an_owing_target_past_its_window_is_refused_not_restarted(rig):
    """A owes frames and its window closed an hour ago (``stop_mode="time"``);
    B is complete and its window is open, so ``window_open`` is true and the
    tick runs the ladder. The run would drop A and skip B, so the ladder
    refuses in words before it solves or slews, and it is not started on the
    next tick a minute later, nor on the retry ten minutes on.

    RED under mutant "no gating", which re-centres on A and starts, on every
    tick, observed verbatim:

            assert rig.starts == [], rig.starts
        E   AssertionError: ['s-gating', 's-gating', 's-gating']
        E   assert ['s-gating', ...', 's-gating'] == []
        E     Left contains 3 more items, first extra item: 's-gating'
        E     Use -v to get more diff
    """
    site = rig.hub.site
    t = _dark_clock(site)
    a = _target("t-a", "NGC 604", *NGC604, stop_mode="time",
                stop_time=_hhmm(t - 3600.0))
    b = _target("t-b", "M13", *M13)
    s = _session([a, b], frames=_banked(b, 3))
    assert _state(a, site, t)["state"] == "window_closed", "premise: A closed"
    assert s.remaining()[a.steps[0].id] == 3, "premise: A owes"
    assert s.remaining()[b.steps[0].id] == 0, "premise: B is complete"
    assert window_open(s, site, TWILIGHT, t), (
        "premise: B's open window opens the session's")
    session_store.save(s)

    for when in (t, t + CHECK_INTERVAL_S, t + RETRY_INTERVAL_S):
        rig.clock["t"] = when
        await rig.arm.tick()

    assert rig.starts == [], rig.starts
    assert rig.solves == [] and rig.gotos == [], (rig.solves, rig.gotos)
    assert rig.arm.hold["reason"] == NOTHING_TONIGHT, rig.arm.hold
    assert numeric_tokens(NOTHING_TONIGHT) == numeric_tokens(""), (
        NOTHING_TONIGHT)
    assert rig.arm._retry_at == t + 2 * RETRY_INTERVAL_S, rig.arm._retry_at


async def test_a_target_that_never_clears_its_floor_tonight_is_dropped(rig):
    """A target 30 degrees south of the equator with an 85 degree start floor
    never clears it from this site: the run drops it (``never_rises``), so
    a session that owes only it has nothing to shoot tonight, and the ladder
    refuses before the solve instead of solving and then refusing on its
    floor every ten minutes all night.

    RED under mutant "no gating", which solves and then refuses on the
    floor, observed verbatim:

            assert refusal == NOTHING_TONIGHT, refusal
        E   AssertionError: south is below its start floor; not slewing yet
        E   assert 'south is bel...t slewing yet' == 'nothing this...he next night'
        E     - nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night
        E     + south is below its start floor; not slewing yet
    """
    site = rig.hub.site
    t = _dark_clock(site)
    a = _target("t-a", "south", *SOUTH, floor=85.0)
    assert _state(a, site, t)["state"] == "never_rises", "premise"

    refusal = await rig.arm._recover(_session([a]))

    assert refusal == NOTHING_TONIGHT, refusal
    assert rig.solves == [] and rig.gotos == [], (rig.solves, rig.gotos)


# ---------------------------------------------- (2) ready before waiting

async def test_a_clock_waiting_panel_gives_way_to_a_ready_one(rig):
    """The walk sorts by window start, so a target waiting on the clock only
    ever stands before a ready one inside a group, whose panels come in
    panel order. Panel 1-1 is the least complete, so the order puts it
    first, but its window opens in two hours; 1-2's is open. The run takes
    the first READY target, 1-2, and so does the ladder.

    RED under mutant "no gating", which re-centres on 1-1, observed
    verbatim:

            assert rig.gotos == [_at(p12)], rig.gotos
        E   AssertionError: [(0.7123, 41.269)]
        E   assert [(0.7123, 41.269)] == [(0.7723, 41.269)]
        E     At index 0 diff: (0.7123, 41.269) != (0.7723, 41.269)
        E     Use -v to get more diff
    """
    site = rig.hub.site
    t = _dark_clock(site)
    group = TargetGroup(id="g-m31", name="M31 mosaic", order="least_complete",
                        rotate=False, geometry={"rows": 1, "cols": 2})
    p11 = _target("p-1-1", "M31 1-1", *M31, group=group.id, row=0, col=0,
                  start_mode="time", start_time=_hhmm(t + 7200.0))
    p12 = _target("p-1-2", "M31 1-2", M31[0] + 0.06, M31[1], group=group.id,
                  row=0, col=1)
    s = _session([p11, p12], groups=[group], frames=_banked(p12, 1))
    waiting, ready = _state(p11, site, t), _state(p12, site, t)
    assert waiting["state"] == "waiting" and waiting["start_ts"] > t, waiting
    assert ready["state"] == "ready", ready
    rig.clock["t"] = t

    assert await rig.arm._recover(s) is None
    assert rig.gotos == [_at(p12)], rig.gotos


async def test_a_constraint_waiting_target_gives_way_to_a_ready_one(rig):
    """A comes first in the plan and both windows opened "now", so the walk
    keeps plan order; but A may be imaged only within an hour of the
    meridian and is still two to four hours east of it. The run passes a
    constraint-waiter over while another target is ready, so the ladder
    re-centres on B.

    RED under mutant "no gating", which re-centres on A, observed verbatim:

            assert rig.gotos == [_at(b)], rig.gotos
        E   AssertionError: [(1.572, 30.7853)]
        E   assert [(1.572, 30.7853)] == [(16.6948, 36.4613)]
        E     At index 0 diff: (1.572, 30.7853) != (16.6948, 36.4613)
        E     Use -v to get more diff
    """
    site = rig.hub.site
    lat, lon = schedule._lat_lon(site)
    a = _target("t-a", "NGC 604", *NGC604, max_ha=1.0)
    b = _target("t-b", "M13", *M13)
    for i in range(3 * 24 * 12):
        t = T0 + i * 300.0
        if -4.0 < schedule.hour_angle_h(a.ra_hours, lon, t) < -2.0:
            break
    else:
        raise AssertionError("no clock reading puts A two to four hours east")
    gated = _state(a, site, t)
    assert gated["state"] == "waiting", gated
    assert gated["start_ts"] is None, "premise: a constraint wait, not clock"
    assert _state(b, site, t)["state"] == "ready", "premise: B is ready"
    rig.clock["t"] = t

    assert await rig.arm._recover(_session([a, b])) is None
    assert rig.gotos == [_at(b)], rig.gotos


async def test_the_gating_resolves_windows_at_the_operators_twilight(
        rig, monkeypatch):
    """The run resolves each window at the operator's twilight
    (``cfg.safety.twilight_deg``), so the ladder must too, or the two part
    ways at every dusk and dawn. Here the operator images from civil dusk
    (-6): A starts at dusk and B "now". In the evening, with the Sun between
    -6 and -12, A's window opened minutes ago at -6, so the walk puts A
    first and the run would shoot it; at the -12 default A would still be
    waiting on the clock and B would go first. The ladder re-centres on A.

    RED under mutant "the gating ignores the operator's twilight"
    (``_recover`` hands ``recentre_candidates`` a fixed -12), which
    re-centres on B, observed verbatim:

            assert rig.gotos == [_at(a)], rig.gotos
        E   AssertionError: [(1.572, 30.7853)]
        E   assert [(1.572, 30.7853)] == [(16.6948, 36.4613)]
        E     At index 0 diff: (1.572, 30.7853) != (16.6948, 36.4613)
        E     Use -v to get more diff
    """
    civil = -6.0
    monkeypatch.setattr(config_store.cfg().safety, "twilight_deg", civil)
    site = rig.hub.site
    a = _target("t-a", "M13", *M13, start_mode="dusk")
    b = _target("t-b", "NGC 604", *NGC604)
    for i in range(3 * 24 * 60):
        t = T0 + i * 60.0
        if not (schedule.dark_enough(site, civil, t)
                and not schedule.dark_enough(site, TWILIGHT, t)):
            continue
        at_default = _state(a, site, t)
        if (schedule.gating_status(a, site, civil, t)["state"] == "ready"
                and at_default["state"] == "waiting"
                and at_default["start_ts"] > t):
            break
    else:
        raise AssertionError("no evening clock reading between -6 and -12")
    assert schedule.gating_status(b, site, civil, t)["state"] == "ready"
    s = _session([b, a])
    assert rig.arm._walk(s, t) == [a, b], "premise: A's window opened first"
    rig.clock["t"] = t

    assert await rig.arm._recover(s) is None
    assert rig.gotos == [_at(a)], rig.gotos


# ------------------------------------------------ (3) the unset site, #121

async def test_with_no_site_set_no_gating_is_applied(rig, monkeypatch):
    """Control (#121): the site is not set, so nothing gates the candidates
    and the ladder re-centres on the owing target whose window a set site
    would call closed, as it did before #283. The coordinates stay the
    fixture's, so gating asked regardless of ``is_default`` would drop A and
    refuse: the flag alone decides.

    RED under mutant "gating without the site check" (``site_is_set`` not
    asked), which refuses, observed verbatim:

            assert refusal is None, refusal
        E   AssertionError: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night
        E   assert 'nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past its observing window or never above its start floor; not slewing until the next night' is None
    """
    site = rig.hub.site
    t = _dark_clock(site)
    a = _target("t-a", "NGC 604", *NGC604, stop_mode="time",
                stop_time=_hhmm(t - 3600.0))
    b = _target("t-b", "M13", *M13)
    assert _state(a, site, t)["state"] == "window_closed", "premise"
    monkeypatch.setattr(config_store.cfg().site, "is_default", True)
    assert rig.hub.site["is_default"] is True, "premise: the site is unset"
    rig.clock["t"] = t

    refusal = await rig.arm._recover(_session([a, b], frames=_banked(b, 3)))

    assert refusal is None, refusal
    assert rig.gotos == [_at(a)], rig.gotos
