# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A pass in which every panel fails centring holds the mosaic and strikes no
panel (#534, H4 orchestrator ruling 2; spec 5.1 pass boundary, 5.6 steps 4
and 7, the guide-start pass rule it mirrors; 6.9 and H3 orchestrator ruling
1, the lines in words).

THE NIGHT IT COMES FROM. The first mosaic on the rig (NGC 1499, a 3x2,
2026-09-29) started low in the east behind an obstruction, in moonlit haze.
Every panel failed centring, each miss counted at its own visit, and three
passes (18 minutes) set all six aside for the night, although the target
rose clear within the hour.

WHAT IT DOES NOW. A centring miss is held until its pass closes, as a failed
guide start is (`GroupRun.visit_outcome`). A pass in which at least two
panels were tried and every one failed to centre is the sky's or the
geometry's (`group_rules.centring_pass_verdict`): no panel's failure count
moves, the boundary says so in words, and the group holds
``CENTRING_HOLD_RETRY_S`` through the deferral wait's own path (a waiter the
scheduler waits on, no new ``hold`` value, the idle clock stopping the
mount), and then starts a new pass. A pass in which any panel centred
strikes the ones that missed, at its boundary, as a miss always was.

THE RUNS are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py): a 3x2 of 30 s frames, L and R three times each,
2 h east of the meridian at the fixture site (40 N 74 W, nobody's rig). Its
hops take no fake time, so a pass of misses is six hops at one instant.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-A-r2-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted where
it was seen, verbatim (the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import re

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_ID, GROUP_NAME, T0, Night, grid_plan,
                            group_hub, group_store)  # noqa: F401
from astrodeck.sequence.group_rules import CENTRING, CENTRING_HOLD_RETRY_S
from astrodeck.sequence.session import session_store

#: The six panels in the order the first pass takes them (``least_complete``
#: on an empty ledger walks the grid as a snake), measured on this harness.
PANELS = ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"]
HOLD_WORDS = ("M31: centring failed on every panel tried; holding before the "
              "next pass")


def _misses(until: float, *, spare: str | None = None):
    """Every panel's centring fails while the night's clock is before
    ``until``, save ``spare``, which always centres."""
    def goto(who, n, result):
        if spare is not None and who == f"{GROUP_NAME} {spare}":
            return result
        if engine_mod.time.time() >= until:
            return result
        return {**result, "centered": False, "error_arcmin": None}
    return goto


async def _night(hub, monkeypatch, plan, goto, *, at: float) -> tuple:
    """Run ``plan``, held once at ``T0 + at`` to read the group's failure
    counts and the published state there; returns the night and both."""
    night = Night(hub, monkeypatch, goto=goto)
    try:
        night.arm()
        night.engine.start(plan)
        night.session_id = night.engine._session.id
        assert await night.until(T0 + at), f"the run ended before +{at} s"
        failed = dict(night.engine._group_runs[GROUP_ID].failed)
        state = dict(night.engine.state)
        await night.finish()
        night.done = not night.engine.running
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night, failed, state


def _passes(night: Night) -> list[tuple[float, list[str]]]:
    """The hops grouped by the instant they were made at: with hops that take
    no fake time, a pass of misses is one instant."""
    out: list[tuple[float, list[str]]] = []
    for t, who in night.gotos:
        rel = night.rel(t)
        if not out or out[-1][0] != rel:
            out.append((rel, []))
        out[-1][1].append(who[len(GROUP_NAME) + 1:])
    return out


def _numbers_beyond_the_words(line: str) -> list[str]:
    """The numbers a centring line says beyond its words: the mosaic's name,
    a panel label, the three-strike count, the pass it closed, the panels it
    tried and the ruling's own minutes are words; anything left would be a
    number the site decided (6.9)."""
    s = line.replace(GROUP_NAME, "")
    s = re.sub(r"\b\d-\d\b", "", s)
    s = re.sub(r"\(\d of 3 consecutive\)|on 3 consecutive visits", "", s)
    s = re.sub(r"\b(45|10) minutes\b", "", s)
    s = re.sub(r"in pass \d+|of the \d+ panels", "", s)
    return re.findall(r"\d+(?:\.\d+)?", s)


def _assert_in_words(night: Night) -> None:
    lines = [m for _t, _lvl, m in night.lines if "centring" in m]
    assert lines, "premise: the night said something about the centring"
    for m in lines:
        assert "°" not in m and " deg" not in m and "altitude" not in m, m
        assert _numbers_beyond_the_words(m) == [], m


# ------------------------------------------------------------- the hold

async def test_a_pass_of_misses_on_every_panel_strikes_none_and_holds(
        group_hub, monkeypatch):
    """Every panel of the 3x2 misses its centring until 1500 s in. Passes 1
    to 3 are six misses each, at 0, 600 and 1200 s: each ends in the
    centring hold, no count moves (read at 300 s, inside the first hold:
    every panel's ``failed`` is 0), nothing is set aside, and each next pass
    begins ``CENTRING_HOLD_RETRY_S`` after the last, never sooner. Through
    the hold the published state names it, in words, with no ``hold`` value
    (the deferral wait's own path), and the idle clock stops the mount 120 s
    after the last hop. Pass 4, at 1800 s, centres, and the mosaic
    completes. No line carries an altitude or a number beyond its words.

    RED under mutant "centring deferrals counted inline" (``GroupRun.
    visit_outcome``'s held-centring branch deleted, so a miss falls through
    to the count at its visit, as before H4), observed:

        AssertionError: a count moved in the hold: {'p00': 1, 'p01': 1,
        'p02': 1, 'p10': 1, 'p11': 1, 'p12': 1}
        assert {'p00': 1, 'p01': 1, 'p02': 1, 'p10': 1, 'p11': 1, 'p12': 1}
        == {'p00': 0, 'p01': 0, 'p02': 0, 'p10': 0, 'p11': 0, 'p12': 0}

    RED under mutant "the hold is the deferral wait" (``_close_group_pass``'s
    ``centring_hold`` branch waiting ``DEFER_WAIT_S`` with ``why``
    "deferred"), observed:

        AssertionError: the passes began at [0.0, 300.0, 600.0, 900.0,
        1200.0, 1500.0]
        assert [0.0, 300.0, 600.0, 900.0] == [0.0, 600.0, 1200.0, 1800.0]
    """
    night, failed, state = await _night(
        group_hub, monkeypatch, grid_plan(rows=2, cols=3),
        _misses(T0 + 1500.0), at=300.0)
    assert night.done
    assert failed == {p: 0 for p in failed}, (
        f"a count moved in the hold: {failed}")
    passes = _passes(night)
    starts = [t for t, _who in passes]
    assert starts[:4] == [0.0, 600.0, 1200.0, 1800.0], (
        f"the passes began at {starts[:6]}")
    assert [who for _t, who in passes[:3]] == [PANELS] * 3
    assert all(b - a >= CENTRING_HOLD_RETRY_S
               for a, b in zip(starts[:3], starts[1:4]))
    holds = night.said("the sky or the geometry is to blame")
    assert holds == [
        f"M31: centring failed on every one of the 6 panels tried in pass "
        f"{n}: the sky or the geometry is to blame, not a panel, so no "
        f"panel's failure count moved; holding the mosaic 10 minutes before "
        f"the next pass" for n in (1, 2, 3)], holds
    assert night.said("consecutive") == [], night.said("consecutive")
    assert (state.get("state"), state.get("detail"), state.get("hold")) == (
        "running", HOLD_WORDS, None), state
    stops = [e[0] for e in night.trace if e[1] == "tracking" and e[2] is False]
    assert stops[:3] == [120.0, 720.0, 1320.0], stops
    assert night.stored.set_aside == []
    assert (night.stored.owed(), night.stored.status) == (0, "complete")
    _assert_in_words(night)


async def test_the_hold_ends_with_the_window_and_sets_nothing_aside(
        group_hub, monkeypatch):
    """The panels never centre, and their window closes 30 min in
    (``max_run_min``). The mosaic holds after each pass of misses, at 0,
    600 and 1200 s, and when the window closes at 1800 s the night ends
    there: the panels are skipped with the window, as any target is, still
    owed and never set aside, so the next night takes the whole grid up.
    Holding is within the night: nothing waits past the window.

    RED under mutant "the hold is the deferral wait" as well, observed:

        assert [0.0, 300.0, ...200.0, 1500.0] == [0.0, 600.0, 1200.0]
          At index 1 diff: 300.0 != 600.0
    """
    plan = grid_plan(rows=2, cols=3, panel_kw={"schedule_kw": {
        "max_run_min": 30}})
    night, failed, _state = await _night(
        group_hub, monkeypatch, plan, _misses(T0 + 10 * 3600.0), at=300.0)
    assert night.done
    assert failed == {p: 0 for p in failed}
    assert [t for t, _who in _passes(night)] == [0.0, 600.0, 1200.0]
    end = max(e[0] for e in night.trace if e[1] == "state")
    assert end == 1800.0, f"the run ended at {end} s"
    assert night.stored.set_aside == []
    assert night.stored.status == "dormant" and night.stored.owed() == 36


# ---------------------------------------------------------------- control

async def test_a_pass_in_which_one_panel_centres_strikes_the_others(
        group_hub, monkeypatch):
    """CONTROL: 1-1 always centres; the other five miss until 1500 s in.
    Every pass has a panel that centred, so each miss is its panel's and is
    counted at the pass boundary, as a miss always was: the counts stand at
    3 after the third pass, the five are set aside (each a CENTRING
    set-aside, the kind that may expire, #534), and no hold is said. 1-1
    completes on its own; the five, expired 45 minutes after they were set
    aside, centre then and complete."""
    night, failed, _state = await _night(
        group_hub, monkeypatch, grid_plan(rows=2, cols=3),
        _misses(T0 + 1500.0, spare="1-1"), at=300.0)
    assert night.done
    assert failed == {"p00": 0, "p01": 3, "p02": 3, "p10": 3, "p11": 3,
                      "p12": 3}, failed
    assert night.said("the sky or the geometry is to blame") == []
    counted = night.said("consecutive)")
    assert [m.split("(")[-1] for m in counted] == (
        ["1 of 3 consecutive)"] * 5 + ["2 of 3 consecutive)"] * 5), counted
    firsts = [r for r in night.stored.set_aside]
    assert sorted(r["target_id"] for r in firsts) == [
        "p01", "p02", "p10", "p11", "p12"]
    assert {r["kind"] for r in firsts} == {CENTRING}
    assert all(r.get("expired") for r in firsts)
    assert (night.stored.owed(), night.stored.status) == (0, "complete")
    _assert_in_words(night)
