# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A pause opened inside a frame-loop cloud hold runs no setup (#263, the
#241 class, H3 orchestrator ruling 4).

H3 made a hold opened by `_setup_target`'s pre-slew gate return to that
setup when it releases, so the target is acquired once. The flag that says a
setup waits (``_acquisition_behind_gate``) was set around two gate calls:
the setup's pre-slew gate and the gate a hold's re-point asks. Not around the
gate the hold's own loop asks on every pass. So in a hold opened from the
frame loop, by a ``hold_for_clear`` rule on a rig with a safety monitor, a
monitor that turned unsafe during the hold opened the open-sky pause from
inside the hold loop, and when the pause released it ran `_setup_target` for
the held target: a slew, a centring solve, a focus sweep and a guider start,
under the cloud the hold was still waiting out. When the sky cleared, the
hold's own release ran `_setup_target` again. Two setups for one
acquisition, and the first under cloud; the roof reopen
(`_await_safe_and_reopen`) had the same shape.

THE FIX. The flag is set around the hold loop's gate too, saved and put back
in a finally. The pause or the roof reopen then returns to the hold without a
setup; the mount it stopped is re-pointed by the hold's stopped-tracking
branch (#248, H3 orchestrator ruling 6), a slew with no centring; and the
hold's release makes the one acquisition when the sky clears.

THE HARNESS is test_cloud_hold_watch's `_Watched` (the clocked simulator,
the sky a script), with the simulator's safety monitor put back, its reading
a script on the fake clock (test_one_setup_per_acquisition's `_Monitor`), and
a ``hold_for_clear`` rule on "clouds in", which is what opens the hold from
the frame loop on a rig with a monitor. Setups, centrings and sweeps are
counted by that file's `_Acquisitions`. The control, a hold opened by a
setup's gate and the frame-loop hold with no pause in it, is
test_one_setup_per_acquisition.py. The site is a fixture.
"""
from __future__ import annotations

from astrodeck.sequence.models import Instruction

from test_cloud_hold_watch import EXP, _Watched, _plan
from test_idle_park_hold import _ra_at, sim_hub, temp_store  # noqa: F401
from test_one_setup_per_acquisition import _Acquisitions, _Monitor, _target

#: Fake seconds from the start of the night. Alpha's own setup is at 0 and
#: its first frame ends at ``EXP``, where the rule opens the hold; the hold's
#: second pass through its gate falls inside the rain, the rain stops at
#: ``SAFE_AT``, and the sky clears at ``CLEARS_AT``, long after.
RAIN_FROM = 100.0
SAFE_AT = 250.0
CLEARS_AT = 400.0
HORIZON = 900.0


def _night(hub, store, monkeypatch, *, roof: bool = False):
    """The frame-loop hold on a rig with a monitor: the rule, the monitor,
    the roof when ``roof`` (closing on unsafe, reopening when safe)."""
    mon = hub.devices.get("safety")
    focuser = hub.devices.get("focuser")
    assert mon is not None and mon.connected, (
        "premise: the sim has a safety monitor")
    safety = dict(on_unsafe="pause", unsafe_consecutive=1,
                  resume_safe_consecutive=1, max_pause_min=0)
    if roof:
        dome = hub.devices.get("dome")
        assert dome is not None and dome.connected, "premise: a roof"
        safety.update(close_dome_on_unsafe=True, reopen_dome_when_safe=True)
    w = _Watched(hub, store, monkeypatch, horizon_s=HORIZON,
                 clears_at_s=CLEARS_AT, safety=safety)
    hub.devices["safety"] = mon            # #263's rig HAS a monitor
    hub.devices["focuser"] = focuser       # so every setup sweeps
    _Monitor(w.run, monkeypatch, unsafe_from=RAIN_FROM, safe_at=SAFE_AT)
    got = _Acquisitions(w.run, monkeypatch)
    alpha = _target("Alpha", _ra_at(-2.0, w.t0), 60.0)
    plan = _plan(alpha, flip=False)
    plan.instructions = [Instruction(trigger="on_clouds_in",
                                     action="hold_for_clear",
                                     message="clouds")]
    return w, got, alpha, plan


def _one_acquisition(w, got, alpha, bus_lines, *, released_by: str) -> None:
    """The shared verdict: the hold opened from the frame loop, the pause or
    roof opened inside it and released, and then exactly one setup, after
    the hold's release, with no centring and no sweep before the sky
    cleared."""
    t_h = w.hold_started()
    assert got.of("Alpha")[:1] == [w.t0] and abs(t_h - (w.t0 + EXP)) < 1.0, (
        f"premise: Alpha's own setup, then the rule's hold as its first "
        f"frame ended: setups at {w.rel(got.of('Alpha'), w.t0)} s, the hold "
        f"at {t_h - w.t0:.1f} s")
    msgs = [m for _l, m, _s in bus_lines]
    assert any(m.startswith("UNSAFE: rain sensor") for m in msgs), (
        "premise: the monitor's rain reached a gate")
    paused = [t for t, st, _h, _d in w.states if st == "paused"]
    assert paused and t_h < paused[0] < w.clears_at, (
        f"premise: the pause opened inside the hold, before the sky cleared: "
        f"{w.rel(paused[:1], w.t0)} s")
    released = w.released_at()
    assert released is not None, "premise: the sky cleared and it released"
    setups = [t for t in got.of("Alpha") if t > t_h]
    early = [t for t in got.at(got.centrings, alpha.ra_hours)
             if t_h < t < w.clears_at]
    swept = [t for t in got.swept("Alpha") if t_h < t < w.clears_at]
    assert len(setups) == 1 and not early and not swept, (
        f"Alpha was set up {len(setups)} times after the frame-loop hold "
        f"opened, at {w.rel(setups, w.t0)} s (the hold released at "
        f"{released - w.t0:.1f} s); centrings under cloud at "
        f"{w.rel(early, w.t0)} s, sweeps under cloud at "
        f"{w.rel(swept, w.t0)} s")
    assert setups[0] >= released - 0.5, (
        f"the one setup was not the hold's release: at "
        f"{setups[0] - w.t0:.1f} s, released at {released - w.t0:.1f} s")
    said = [m for m in msgs if f"through the acquisition the {released_by} "
                               f"interrupted" in m]
    assert len(said) == 1, (
        f"the release inside the hold did not say it returned to the hold: "
        f"{said}")
    assert w.engine._acquisition_behind_gate is None, (
        "the flag outlived the gate it was set around")


async def test_a_pause_inside_a_frame_loop_hold_runs_no_setup(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """#263's reproduction. The rule opens a hold on Alpha as its first frame
    ends. The monitor reads rain from 100 s to 250 s, so the hold loop's own
    gate opens the open-sky pause, which stops tracking and waits. When the
    rain stops, the pause returns to the hold without a setup; the hold
    points the stopped mount back at Alpha and goes on judging the sky, and
    when the sky clears at 400 s its release acquires Alpha, once.

    Mutant "flag not set around the hold loop's gate" (the
    ``self._acquisition_behind_gate = target`` before the hold loop's
    ``_safety_gate`` call deleted, which is the code before the fix): RED
    (observed) -
        AssertionError: Alpha was set up 2 times after the frame-loop hold
        opened, at [250.0, 550.0] s (the hold released at 550.0 s);
        centrings under cloud at [250.0] s, sweeps under cloud at [250.0] s
    """
    w, got, alpha, plan = _night(sim_hub, temp_store, monkeypatch)
    try:
        await w.night(plan)
        _one_acquisition(w, got, alpha, bus_lines, released_by="pause")
    finally:
        await w.close()


async def test_a_roof_reopen_inside_a_frame_loop_hold_runs_no_setup(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """The roof, the same shape. With the roof closing on unsafe and
    reopening when safe, the hold loop's gate parks the mount and closes the
    roof over it; when the rain stops the roof reopens and returns to the
    hold without a setup. The hold finds the mount parked, a stop the engine
    made, and re-points it (an unpark and a slew, no centring), and its
    release acquires Alpha once when the sky clears.

    Mutant "flag not set around the hold loop's gate" (as above): RED
    (observed) -
        AssertionError: Alpha was set up 2 times after the frame-loop hold
        opened, at [250.0, 550.0] s (the hold released at 550.0 s);
        centrings under cloud at [250.0] s, sweeps under cloud at [250.0] s
    """
    w, got, alpha, plan = _night(sim_hub, temp_store, monkeypatch, roof=True)
    try:
        await w.night(plan)
        msgs = [m for _l, m, _s in bus_lines]
        assert any("reopening roof" in m for m in msgs), (
            "premise: the roof closed and reopened")
        _one_acquisition(w, got, alpha, bus_lines,
                         released_by="roof close")
        assert w.run.tracking(), "premise: the one setup left Alpha tracked"
    finally:
        await w.close()
