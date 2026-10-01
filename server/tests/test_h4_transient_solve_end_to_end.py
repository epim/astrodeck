"""A solve frame another process holds, end to end: the real hub's write
fails with a sharing violation on every retry, and the panel it cost a
centring is not struck for it (#532; H4 orchestrator contract 1; spec 5.1's
table, 5.6 step 4).

THE NIGHT IT COMES FROM. 2026-09-29, the first rig mosaic: an agent
copying the last solve frame off the rig held ``captures/_solve/solve.fits``
open just as the engine wrote the next one there, the solve failed with
"[WinError 32] The process cannot access the file because it is being used
by another process", and the panel's centring counted it toward the
three-strike set-aside. Nothing about the panel or the sky had failed.

THE TWO HALVES, AND THIS FILE. H4-HUB made each solve frame a name of its
own, retried a sharing violation under a fresh name, and said so in the
goto's result (``solve_transient: True``) once the retries ran out
(tests/test_h4_solve_frame_unique_names.py). H4-ENG-A made the engine defer
such a visit without counting it (tests/test_h4_transient_solve_is_no_
strike.py), on a harness double that answers in the contract's shape. This
case joins them: nothing between the held file and the panel's count is a
double.

THE NIGHT is the clocked simulator (tests/_group_harness.py): a 2x2 of 30 s
frames, L and R three times each, 2 h east of the meridian. Panel 2-2's first
four hops, one more than ``max_failed_visits``, go through the REAL
`Hub.goto_and_center` (its slew, its `solve_and_sync`, the simulator
camera's exposure and `hub._write_solve_frame`), with ``hub.save_fits``
raising a ``PermissionError`` whose ``winerror`` is 32 on every call, the
seam H4-HUB named (``winerror`` set by hand, so it is the same on any OS).
Every other hop is the harness's scripted centring. The retries' backoff is
cut to a millisecond; it is real time, not the night's.

The mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after it,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import astrodeck.hub as hub_mod
from _group_harness import (GROUP_ID, GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.hub import Hub
from astrodeck.sequence.session import session_store

LABEL = "2-2"
PANEL = f"{GROUP_NAME} {LABEL}"
PANEL_ID = "p11"
#: How many of the panel's hops meet a held file: one more than
#: ``max_failed_visits`` (3), so three counted strikes would set it aside.
HELD_HOPS = 4
#: The centring solve's exposure on the held hops, in real seconds: the
#: simulator camera sleeps it out, and the write fails after it anyway.
SOLVE_EXPOSURE_S = 0.01


def _sharing_violation() -> PermissionError:
    """What Windows raises for a file another process holds open."""
    e = PermissionError(13, "The process cannot access the file because it "
                            "is being used by another process")
    e.winerror = 32
    return e


async def _night(hub, monkeypatch):
    night = Night(hub, monkeypatch)
    scripted_goto = hub.goto_and_center
    monkeypatch.setattr(hub_mod, "SOLVE_WRITE_BACKOFF_S", 0.001)
    held = {"on": False}
    writes: list[str] = []
    real_save = hub_mod.save_fits

    def save_fits(*a, **kw):
        if held["on"]:
            writes.append(str(night.engine.state.get("target", "")))
            raise _sharing_violation()
        return real_save(*a, **kw)

    monkeypatch.setattr(hub_mod, "save_fits", save_fits)
    results: list[dict] = []

    async def goto_and_center(ra_hours, dec_deg, *args, rotation_deg=None,
                              **kw):
        who = str(night.engine.state.get("target", ""))
        if who != PANEL or len(results) >= HELD_HOPS:
            return await scripted_goto(ra_hours, dec_deg, *args,
                                       rotation_deg=rotation_deg, **kw)
        # The harness's own hop record, which its scripted goto keeps.
        night.gotos.append((night.clock.t, who))
        held["on"] = True
        try:
            result = await Hub.goto_and_center(
                hub, ra_hours, dec_deg, *args, rotation_deg=rotation_deg,
                **{**kw, "solve_exposure_s": SOLVE_EXPOSURE_S})
        finally:
            held["on"] = False
        results.append(result)
        return result

    monkeypatch.setattr(hub, "goto_and_center", goto_and_center)
    counts: list[int] = []
    night.on_capture = lambda rec: counts.append(
        night.engine._group_runs[GROUP_ID].failed.get(PANEL_ID, 0))
    try:
        night.done = await night.run(grid_plan(), wall_s=120.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night, results, writes, counts


async def test_a_held_solve_frame_never_strikes_the_panel(group_hub,
                                                          monkeypatch):
    """Panel 2-2's first four centrings each meet a solve frame another
    process holds, on every one of the hub's bounded retries: each real
    goto comes back ``solve_failed`` with ``solve_transient``, and the
    panel's visit is deferred without counting. Its ``failed`` reads 0 at
    every exposure of the night, no set-aside follows (nothing recorded,
    nothing said), its fifth hop centres, and the mosaic completes.

    RED under mutant "hub drops solve_transient" (`Hub.goto_and_center`'s
    solve-failure return without its ``| ({"solve_transient": True} if
    isinstance(e, SolveFrameTransient) else {})``), observed:

        AssertionError: a solve frame another process held struck 2-2:
        its failure count read [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2,
        2, 2, 2, 1, 1, 0, 0, 0, 0] across the night
    """
    night, results, writes, counts = await _night(group_hub, monkeypatch)
    assert night.done, night.lines[-3:]
    assert len(results) == HELD_HOPS and all(
        r.get("solve_failed") and r.get("centered") is False
        for r in results), f"premise: every held hop's solve failed: {results}"
    assert len(writes) == HELD_HOPS * hub_mod.SOLVE_WRITE_ATTEMPTS and set(
        writes) == {PANEL}, (
        f"premise: the hub tried each held frame {hub_mod.SOLVE_WRITE_ATTEMPTS} "
        f"times, only on {PANEL}: {len(writes)} writes, {set(writes)}")
    assert counts and counts == [0] * len(counts), (
        f"a solve frame another process held struck {LABEL}: its failure "
        f"count read {counts} across the night")
    assert night.stored.set_aside == [], night.stored.set_aside
    assert night.said("set aside") == [], night.said("set aside")
    hops = [night.rel(t) for t, name in night.gotos if name == PANEL]
    assert len(hops) == HELD_HOPS + 3, (
        f"premise: four held hops, then its three visits: {hops}")
    assert (night.stored.owed(), night.stored.status) == (0, "complete"), (
        night.stored.owed(), night.stored.status)
