# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#860 (hub half): a goto that did not arrive is a centring miss, never a
run-ending raise.

The AM5 driver now raises ``GotoNotArrived`` when the mount accepted a goto
and stopped short, or a stop was sent during it. ``Hub.goto_and_center``
absorbs it: the field is solved where the mount stopped and the loop goes on
(ruling R1), the result says ``goto_not_arrived`` with the driver's fixed
words, and a miss that came with the motion epoch moved (a STOP or the
manual-move deadman) returns the aborted shape unsolved. A miss on the rotate
pre-slew skips the rotation and centres.

These drive the REAL ``goto_and_center`` on the sim hub, with only the sim
telescope's ``slew`` replaced (the real signature) and a fixed solver. Every
coordinate is the fictional, non-round set of ``test_850_hub_sync_refused``.
Each test names the mutant of hub.py it was shown red under; each mutant was
applied to a byte copy and the file restored from that copy (sha256 checked).
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_850_hub_sync_refused import (FAR_DEC, FAR_RA, NEAR_DEC, NEAR_RA, DEC,
                                       RA, _assert_surfaced_ok)

from astrodeck.catalog import coords
from astrodeck.config import config_store
from astrodeck.devices.base import GotoNotArrived
from astrodeck.hub import Hub
from astrodeck.solve.base import SolveResult

#: Stand-ins for the driver's fixed words (no figure, no code).
_STALLED = "the mount stopped short of the target"
_STOPPED = "a stop was sent during the goto"

#: 2026-06-14 12:00 UTC. The Sun is then 13.0 deg from (RA, DEC), inside the
#: 30 deg default cone; the target sits in the cone from about May 15 to
#: Jul 10 every year.
_JUNE_NOON = 1781438400.0


@pytest.fixture(autouse=True)
def _cone_disarmed_with_the_sun_on_the_target(sim_hub, monkeypatch):
    """The sun-exclusion cone is disarmed, and the real ``_check_solar`` sees
    the June Sun every day, so the file cannot pass by the calendar.

    ``sim_hub`` leaves ``solar_avoidance`` at its default True, and
    ``goto_and_center`` checks the cone first, so from mid-May to early July
    every case here raised "target is within 13 deg of the Sun" before any
    scripted slew ran (the #682 class keyed on the date). The Sun is pinned
    only inside ``_check_solar``; ``sun_altaz`` and the rest keep the clock.

    NAMED MUTANT C-M1 "cone armed" (the ``solar_avoidance`` line below
    deleted): RED on any date, all 14 cases, ``DeviceError: target is within
    13 deg of the Sun (exclusion 30 deg)``."""
    june = coords.sun_radec(_JUNE_NOON)
    cone = config_store.cfg().safety.solar_exclusion_deg
    assert coords.angular_sep_deg(RA, DEC, *june) < cone, (
        "the pinned Sun no longer sits in the cone, so this fixture proves "
        "nothing about the calendar")
    real = Hub._check_solar

    def _check_solar_in_june(self, ra_hours, dec_deg, *, force=False):
        with monkeypatch.context() as m:
            m.setattr(coords, "sun_radec", lambda unix_time=None: june)
            return real(self, ra_hours, dec_deg, force=force)

    monkeypatch.setattr(Hub, "_check_solar", _check_solar_in_june)
    monkeypatch.setattr(config_store.cfg().safety, "solar_avoidance", False)


class _CountingSolver:
    """A solver that always finds the field at one place, and counts."""
    name = "Fixed"

    def __init__(self, ra: float, dec: float):
        self.ra, self.dec = ra, dec
        self.calls = 0

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        self.calls += 1
        return SolveResult(True, ra_hours=self.ra, dec_deg=self.dec,
                           pixel_scale_arcsec=1.55, message="fixed")


def _solver(monkeypatch, ra: float, dec: float) -> _CountingSolver:
    import astrodeck.providers as providers_module
    solver = _CountingSolver(ra, dec)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)
    return solver


def _scripted_slews(hub, monkeypatch, script: list[str]) -> list[str]:
    """Replace the sim telescope's ``slew`` (on the instance, the real
    signature): each call takes the next entry of ``script`` (the last one
    repeats). ``"miss"`` raises a stalled ``GotoNotArrived``, ``"stop"``
    bumps the motion epoch (what STOP and the deadman do before their halt)
    and raises a stopped one, ``"real"`` calls the sim's own slew. Returns
    the entries taken."""
    tel = hub.devices["telescope"]
    real = tel.slew
    taken: list[str] = []

    async def slew(ra_hours, dec_deg):
        what = script[min(len(taken), len(script) - 1)]
        taken.append(what)
        if what == "miss":
            raise GotoNotArrived("Sim: goto did not arrive (stalled)",
                                 reason=_STALLED, residual_deg=3.21)
        if what == "stop":
            hub.bump_motion_epoch()
            raise GotoNotArrived("Sim: goto did not arrive (stopped)",
                                 reason=_STOPPED, residual_deg=3.21)
        await real(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "slew", slew)
    return taken


def _missed_lines(bus_lines) -> list[tuple[str, str, str]]:
    return [(lvl, m, s) for lvl, m, s in bus_lines
            if "the goto did not arrive" in m]


async def test_a_stalled_goto_is_solved_where_it_stopped_never_raised(
        sim_hub, monkeypatch, bus_lines):
    """Every centring slew stalls and the field solves 2.5 deg off: no raise
    out of ``goto_and_center``; the miss is solved where it stopped; the
    stuck check ends it at attempt 2 with the arrival keys on the result,
    and no ``solve_failed``.

    NAMED MUTANT H-M1 "uncaught" (the loop's ``except GotoNotArrived`` made
    ``except ZeroDivisionError``): RED, ``GotoNotArrived`` raised out of
    ``goto_and_center``.

    NAMED MUTANT H-M2 "key dropped" (``| arrival_keys`` removed from the
    stuck return): RED, ``KeyError: 'goto_not_arrived'``.
    """
    _solver(monkeypatch, FAR_RA, FAR_DEC)
    taken = _scripted_slews(sim_hub, monkeypatch, ["miss"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is False, result
    assert result["goto_not_arrived"] is True, result
    assert result["goto_reason"] == _STALLED
    assert result["error_arcmin"] == pytest.approx(150.0, abs=0.05)
    assert "solve_failed" not in result, result
    assert result.get("did_not_move") is True, result
    assert taken == ["miss", "miss"], taken

    missed = _missed_lines(bus_lines)
    assert len(missed) == 2, missed
    for lvl, m, s in missed:
        assert (lvl, s) == ("warning", "mount"), (lvl, s)
        assert len(m) <= 137, (len(m), m)
        assert "3.21 deg off" in m, m
        _assert_surfaced_ok(m, (RA, DEC), (FAR_RA, FAR_DEC))


async def test_a_missed_goto_that_solves_on_target_is_centred(
        sim_hub, monkeypatch, bus_lines):
    """The mount stopped short, but the solve says the field is within
    tolerance: centred, like any other landing, and no arrival keys.

    NAMED MUTANT H-M3 "stop on a miss" (a not-centred ``return`` right after
    the miss warning): RED, ``assert False is True`` on ``centered``.
    """
    _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    taken = _scripted_slews(sim_hub, monkeypatch, ["miss", "real"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is True, result
    assert "goto_not_arrived" not in result, result
    assert taken == ["miss"], taken
    assert len(_missed_lines(bus_lines)) == 1


async def test_a_miss_with_the_epoch_moved_returns_aborted_unsolved(
        sim_hub, monkeypatch, bus_lines):
    """A stop (or the deadman) bumps the motion epoch before its halt, so a
    goto it ended comes back with the epoch moved: aborted, with the arrival
    keys, and no solve of a field nobody asked for.

    NAMED MUTANT H-M4 "no fence check" (the ``_motion_committed_clean``
    check after the miss made ``if False:``): RED, the solver is called
    (``assert 1 == 0``).
    """
    solver = _solver(monkeypatch, FAR_RA, FAR_DEC)
    _scripted_slews(sim_hub, monkeypatch, ["stop"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result.get("aborted") is True, result
    assert result["centered"] is False
    assert result["goto_not_arrived"] is True, result
    assert result["goto_reason"] == _STOPPED
    assert solver.calls == 0
    assert _missed_lines(bus_lines) == []


def _rotate_set_up(hub, monkeypatch) -> dict:
    """The rotate pre-slew path: a connected sim rotator, the shortcut off,
    the preflight a no-op, and ``rotate_to_pa`` counted."""
    assert hub.devices["rotator"].connected
    calls = {"rotate": 0, "ready": 0}

    async def not_set(rot, rotation_deg):
        return None

    async def ready():
        calls["ready"] += 1

    async def rotate_to_pa(rotation_deg, exposure_s=None):
        calls["rotate"] += 1
        return {"pa_deg": rotation_deg}

    monkeypatch.setattr(hub, "_rotation_already_set", not_set)
    monkeypatch.setattr(hub, "ensure_rotator_ready", ready)
    monkeypatch.setattr(hub, "rotate_to_pa", rotate_to_pa)
    return calls


async def test_a_missed_rotate_pre_slew_skips_the_rotation_and_centres(
        sim_hub, monkeypatch, bus_lines):
    """The pre-slew that puts the target's field on the sensor for the
    rotate loop stops short: the rotation is skipped (it would solve the
    wrong sky), one warning says so, and the centring attempts re-slew and
    centre.

    NAMED MUTANT H-M5 "pre-slew uncaught" (the pre-slew's ``except
    GotoNotArrived`` made ``except ZeroDivisionError``): RED,
    ``GotoNotArrived`` raised out of ``goto_and_center``.
    """
    calls = _rotate_set_up(sim_hub, monkeypatch)
    _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    taken = _scripted_slews(sim_hub, monkeypatch, ["miss", "real"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05,
                                           rotation_deg=30.0)

    assert result["centered"] is True, result
    assert result.get("rotation_skipped") is True, result
    assert taken == ["miss", "real"], taken
    assert calls == {"rotate": 0, "ready": 0}, calls
    rot_lines = [(lvl, m) for lvl, m, s in bus_lines
                 if s == "rotator" and "rotation to PA" in m]
    assert len(rot_lines) == 1, rot_lines
    assert rot_lines[0][0] == "warning"
    assert "skipped" in rot_lines[0][1] and _STALLED in rot_lines[0][1]
    assert len(rot_lines[0][1]) <= 137


async def test_a_missed_pre_slew_after_a_stop_returns_aborted(
        sim_hub, monkeypatch, bus_lines):
    """The pre-slew ended by a stop (the epoch moved): aborted at once, with
    the arrival keys and the stopped words, no rotation warning, the rotator
    never asked and the solver never called.

    NAMED MUTANT H-M6 "no pre-slew fence" (the ``_motion_committed_clean``
    check inside ``if pre_missed is not None:`` made ``if False:``): RED,
    the skip warning is logged and the loop-top abandon returns without
    ``goto_not_arrived`` (``KeyError``).
    """
    calls = _rotate_set_up(sim_hub, monkeypatch)
    solver = _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _scripted_slews(sim_hub, monkeypatch, ["stop", "real"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05,
                                           rotation_deg=30.0)

    assert result.get("aborted") is True, result
    assert result["goto_not_arrived"] is True, result
    assert result["goto_reason"] == _STOPPED
    assert result["rotation"] is None
    assert calls == {"rotate": 0, "ready": 0}, calls
    assert solver.calls == 0
    assert not any("rotation to PA" in m for _, m, _ in bus_lines), bus_lines


# ------------------------------------------------- fix round 1: tracking again


def _order_log(hub, monkeypatch, solver) -> list[str]:
    """Interleave, in one list, every slew, every ``set_tracking`` call and
    every solve, wrapping what is already installed (the scripted slew, the
    sim's ``set_tracking``, the counting solver), with the real signatures."""
    tel = hub.devices["telescope"]
    log: list[str] = []
    slew, track, solve = tel.slew, tel.set_tracking, solver.solve

    async def logged_slew(ra_hours, dec_deg):
        log.append("slew")
        await slew(ra_hours, dec_deg)

    async def logged_track(on):
        log.append(f"track:{on}")
        await track(on)

    async def logged_solve(fits_path, *, ra_hint=None, dec_hint=None,
                           fov_deg_hint=None):
        log.append("solve")
        return await solve(fits_path, ra_hint=ra_hint, dec_hint=dec_hint,
                           fov_deg_hint=fov_deg_hint)

    monkeypatch.setattr(tel, "slew", logged_slew)
    monkeypatch.setattr(tel, "set_tracking", logged_track)
    monkeypatch.setattr(solver, "solve", logged_solve)
    return log


async def test_tracking_is_turned_back_on_after_a_miss_before_the_solve(
        sim_hub, monkeypatch, bus_lines):
    """The driver ends a goto that did not arrive with ``:Q#``, and whether
    that stops tracking on the AM5 is not measured. After the miss, and
    before the solve, tracking is asked for again, so a centred return never
    images on a mount the halt left untracked.

    NAMED MUTANT F1-T1 "no re-track in the loop" (the centring loop's
    ``await self._retrack_after_missed_goto(tel, f"centering attempt
    {attempt}")`` deleted): RED, the log reads ``['track:True', 'slew',
    'solve']``.
    """
    solver = _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _scripted_slews(sim_hub, monkeypatch, ["miss", "real"])
    log = _order_log(sim_hub, monkeypatch, solver)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is True, result
    assert log == ["track:True", "slew", "track:True", "solve"], log


async def test_a_refused_re_track_is_one_warning_and_centring_goes_on(
        sim_hub, monkeypatch, bus_lines):
    """Re-asserting tracking is best effort: a mount that refuses it gets one
    warning naming the attempt, and the solve still decides "centred".

    NAMED MUTANT F1-T2 "re-track not tolerant" (the helper's ``except
    Exception as e:`` made ``except ZeroDivisionError as e:``): RED, the
    ``DeviceError`` escapes ``goto_and_center``.
    """
    from astrodeck.devices.base import DeviceError
    _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _scripted_slews(sim_hub, monkeypatch, ["miss", "real"])
    tel = sim_hub.devices["telescope"]
    real_track = tel.set_tracking
    calls: list[bool] = []

    async def set_tracking(on):
        calls.append(on)
        if len(calls) > 1:      # the pre-slew call works; the re-track not
            raise DeviceError("Sim: tracking on refused")
        await real_track(on)

    monkeypatch.setattr(tel, "set_tracking", set_tracking)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is True, result
    assert calls == [True, True], calls
    lines = [(lvl, m, s) for lvl, m, s in bus_lines
             if "tracking could not be turned back on" in m]
    assert len(lines) == 1, lines
    lvl, m, s = lines[0]
    assert (lvl, s) == ("warning", "mount")
    assert m.startswith("centering attempt 1: "), m
    assert len(m) <= 137, (len(m), m)
    _assert_surfaced_ok(m, (RA, DEC), (NEAR_RA, NEAR_DEC))


async def test_a_missed_rotate_pre_slew_turns_tracking_back_on(
        sim_hub, monkeypatch, bus_lines):
    """The rotate pre-slew's miss is halted by the driver too: tracking is
    asked for again before the centring attempts.

    NAMED MUTANT F1-T3 "no re-track on the pre-slew" (the pre-slew branch's
    ``await self._retrack_after_missed_goto(tel, "rotation pre-slew")``
    deleted): RED, the log reads ``['track:True', 'slew', 'slew',
    'solve']``.
    """
    _rotate_set_up(sim_hub, monkeypatch)
    solver = _solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _scripted_slews(sim_hub, monkeypatch, ["miss", "real"])
    log = _order_log(sim_hub, monkeypatch, solver)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05,
                                           rotation_deg=30.0)

    assert result["centered"] is True, result
    assert result.get("rotation_skipped") is True, result
    assert log == ["track:True", "slew", "track:True", "slew", "solve"], log


# ------------------------------- fix round 1: the keys on every not-centred end


class _ScriptSolver:
    """A solver that answers from ``places`` in turn (the last repeats):
    ``None`` is a failed solve, a pair is where the field is. ``on_call``
    runs inside each solve."""
    name = "Scripted"

    def __init__(self, places, on_call=None):
        self.places, self.on_call = places, on_call
        self.calls = 0

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        place = self.places[min(self.calls, len(self.places) - 1)]
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        if place is None:
            return SolveResult(False, message="no stars found")
        return SolveResult(True, ra_hours=place[0], dec_deg=place[1],
                           pixel_scale_arcsec=1.55, message="scripted")


def _script_solver(monkeypatch, places, on_call=None) -> _ScriptSolver:
    import astrodeck.providers as providers_module
    solver = _ScriptSolver(places, on_call)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)
    return solver


#: A field that walks toward the target (2.5, 2.0 then 1.5 deg north): never
#: within tolerance, and never the stuck check's "changed nothing" (each step
#: is 30', far over ``CENTERING_STUCK_ARCMIN``). Fictional, non-round.
_WALK = [(FAR_RA, FAR_DEC), (FAR_RA, DEC + 2.0), (FAR_RA, DEC + 1.5)]


@pytest.mark.parametrize("case", ["solve_failed", "non_finite", "sync_refused",
                                  "exhausted", "loop_top_abandon"])
async def test_every_not_centred_end_after_a_miss_carries_the_keys(
        sim_hub, monkeypatch, bus_lines, case):
    """P2's #852 policy reads ``goto_not_arrived`` from whichever not-centred
    return the loop reaches after a miss. Each case below reaches a
    different one (the distinguishing key is asserted too, so a case cannot
    pass on the wrong return), and each must carry the keys.

    NAMED MUTANTS, ``| arrival_keys`` removed from one return each:
    X4 "solve-failed return" (case ``solve_failed``), X5 "exhausted return"
    (``exhausted``), X6 "sync refused/unverified return" (``sync_refused``),
    X7 "non-finite return" (``non_finite``), X8 "loop-top abandon return"
    (``loop_top_abandon``): each RED in its own case, ``KeyError:
    'goto_not_arrived'``.
    """
    from test_850_hub_sync_refused import _failing_sync
    tel = sim_hub.devices["telescope"]
    _scripted_slews(sim_hub, monkeypatch, ["miss"])
    kwargs: dict = {}
    if case == "solve_failed":
        _script_solver(monkeypatch, [None])
    elif case == "non_finite":
        _script_solver(monkeypatch, [(float("nan"), FAR_DEC)])

        async def sync(ra_hours, dec_deg):     # the sim must not take a NaN
            return None

        monkeypatch.setattr(tel, "sync", sync)
    elif case == "sync_refused":
        _script_solver(monkeypatch, [(FAR_RA, FAR_DEC)])
        _failing_sync(tel, monkeypatch)
    elif case == "exhausted":
        _script_solver(monkeypatch, _WALK)
        kwargs["max_attempts"] = 3
    else:
        # The epoch moves during attempt 1's solve: attempt 2's loop-top
        # fence abandons, after a first slew that missed.
        _script_solver(monkeypatch, [(FAR_RA, FAR_DEC)],
                       on_call=sim_hub.bump_motion_epoch)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05,
                                           **kwargs)

    assert result["centered"] is False, result
    key, value = {
        "solve_failed": ("solve_failed", True),
        "non_finite": ("solve_failed", True),
        "sync_refused": ("sync_refused", True),
        "exhausted": ("attempts", 3),
        "loop_top_abandon": ("aborted", True),
    }[case]
    assert result.get(key) == value, result
    if case == "exhausted":
        assert "did_not_move" not in result, result
    if case == "loop_top_abandon":
        assert result["attempts"] == 1, result
    assert result["goto_not_arrived"] is True, result
    assert result["goto_reason"] == _STALLED


async def test_an_arrived_later_slew_clears_the_keys(
        sim_hub, monkeypatch, bus_lines):
    """The keys describe the LATEST centring slew. Attempt 1 missed, attempts
    2 and 3 arrived, and the field never came within tolerance: the run ends
    exhausted with no ``goto_not_arrived``.

    NAMED MUTANT X3 "sticky key" (``arrival_keys = ({} if missed is None
    else`` made ``arrival_keys = (arrival_keys if missed is None else``):
    RED, ``goto_not_arrived`` is still on the result.
    """
    _script_solver(monkeypatch, _WALK)
    taken = _scripted_slews(sim_hub, monkeypatch, ["miss", "real", "real"])

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05,
                                           max_attempts=3)

    assert taken == ["miss", "real", "real"], taken
    assert result["centered"] is False, result
    assert result["attempts"] == 3, result
    assert "did_not_move" not in result, result
    assert "goto_not_arrived" not in result, result
    assert "goto_reason" not in result, result
