"""A solve that could not run is never a strike against its panel (#532, the
engine half; H4 orchestrator contract 1; spec 5.1's table, 5.6 step 4).

THE NIGHT IT COMES FROM. 2026-09-29 00:52:18, the first rig mosaic: a plate
solve failed with WinError 32 because another process (an agent copying the
last solve frame off the rig) held the fixed solve-frame file open, and the
panel's centring counted it as a failure toward the three-strike set-aside.
Nothing about the panel or the sky had failed.

THE CONTRACT. ``hub.goto_and_center`` says so in its result: ``solve_transient:
True`` beside the key that says what the held file cost, ``centered: False``
(a centring solve) or ``rotation_skipped`` (the rotate loop's solve). The
hub's half, which writes each solve frame to a name of its own and sets the
key only when the bounded retries ran out, is tests/test_h4_solve_frame_
unique_names.py; the whole path through a real held file is H4-ENG-B's.

WHAT THE ENGINE DOES WITH IT. `_group_hop_checks` raises the deferral of kind
``solve_transient`` (``group_rules.SOLVE_TRANSIENT``), which
`GroupRun.visit_outcome` requeues to the next pass without counting: the
panel's ``failed`` does not move, and the visit is left out of the centring
pass rule. It is still a deferral, so a pass of nothing else waits and tries
again.

THE RUNS are the real `_run_scheduled` on the clocked simulator
(tests/_group_harness.py), whose ``goto_and_center`` double answers in the
contract's shape: a 2x2 of 30 s frames, L and R three times each, 2 h east
of the meridian. One panel's first four hops come back transient, one more
than ``max_failed_visits`` (3), so a transient that counted would have set
it aside.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-A-r2-mut``), from a byte backup restored and sha256-checked after
each, never in the shared tree (#254). The observed failure is quoted where
it was seen, verbatim (the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_ID, GROUP_NAME, Night, grid_plan,
                            group_hub, group_store, panel)  # noqa: F401
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import (CENTRING, SOLVE_TRANSIENT,
                                            PanelDeferred)
from astrodeck.sequence.models import TargetGroup
from astrodeck.sequence.session import session_store

#: How many of the panel's first hops come back transient: one more than
#: ``max_failed_visits``, so three counted misses would have set it aside.
TRANSIENT_HOPS = 4
#: The two ways the contract's key rides a result, by the panel it is
#: scripted on, the miss it sits beside, and the group it needs.
CASES = {
    "centring": ("2-2", {"centered": False, "error_arcmin": None}, {}),
    "rotate": ("1-2", {"rotation_skipped": True},
               {"rotate": True, "pa_deg": 30.0}),
}


def _hops(label: str, miss: dict, *, transient: bool):
    """``label``'s first ``TRANSIENT_HOPS`` hops come back with ``miss``,
    carrying the contract's ``solve_transient`` when ``transient``; every
    other hop centres, as the harness answers."""
    def goto(who, n, result):
        if who == f"{GROUP_NAME} {label}" and n <= TRANSIENT_HOPS:
            return {**result, **miss,
                    **({"solve_transient": True} if transient else {})}
        return result
    return goto


async def _night(hub, monkeypatch, case: str, *, transient: bool):
    """The night, with the panel's failure count read at every exposure."""
    label, miss, group_kw = CASES[case]
    pid = f"p{int(label[0]) - 1}{int(label[2]) - 1}"
    night = Night(hub, monkeypatch, goto=_hops(label, miss,
                                               transient=transient))
    counts: list[int] = []
    night.on_capture = lambda rec: counts.append(
        night.engine._group_runs[GROUP_ID].failed.get(pid, 0))
    try:
        night.done = await night.run(grid_plan(group_kw=group_kw))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night, label, counts


# --------------------------------------------------------------- the hop

@pytest.mark.parametrize(("result", "kind"), [
    pytest.param({"centered": False, "solve_transient": True},
                 SOLVE_TRANSIENT, id="centring transient"),
    pytest.param({"centered": False}, CENTRING, id="centring miss"),
    pytest.param({"centered": True, "rotation_skipped": True,
                  "solve_transient": True}, SOLVE_TRANSIENT,
                 id="rotate transient"),
    pytest.param({"centered": True, "rotation_skipped": True}, "rotation",
                 id="rotator skipped"),
])
def test_the_hop_raises_the_deferral_the_contract_names(result, kind):
    """`_group_hop_checks` on the four results a hop can bring back from a
    failed solve: the contract's key makes either miss the kind that does
    not count, and without it each stays the kind it always was (the
    CONTROLS).

    RED under mutant "transient counted as centring" (the ``solve_transient``
    branch of the centring check deleted), observed:

        AssertionError: assert 'centring' == 'solve_transient'

    RED under mutant "rotate transient counted as rotation" (the
    ``solve_transient`` branch of the rotator check deleted), observed:

        AssertionError: assert 'rotation' == 'solve_transient'
    """
    target = panel(0, 1, rotation_deg=30.0)
    group = TargetGroup(id=GROUP_ID, name=GROUP_NAME, rotate=True,
                        pa_deg=30.0)
    miss = None if result["centered"] else "plate solve failed — used raw GoTo"
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(target, group, result, miss)
    assert caught.value.kind == kind


# ------------------------------------------------------------ the night

@pytest.mark.parametrize("case", sorted(CASES))
async def test_a_transient_solve_never_moves_the_panels_count(
        group_hub, monkeypatch, case):
    """The panel's first four hops come back transient. Each visit is
    deferred to the next pass and said in words as not counted; the panel's
    ``failed`` reads 0 at every exposure of the night; it is never set aside;
    its fifth hop centres and it completes, and so does the mosaic.

    RED under mutant "transient counted as centring" (the ``solve_transient``
    branch of `_group_hop_checks`'s centring check deleted), case
    ``centring``, observed:

        AssertionError: 2-2's failure count moved: [0, 0, 0, 0, 0, 0, 1, 1,
        1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 1, 1, 0, 0, 0, 0]

    RED under mutant "rotate transient counted as rotation" (the
    ``solve_transient`` branch of the rotator check deleted), case
    ``rotate``, observed:

        AssertionError: 1-2's failure count moved: [0, 0, 1, 1, 1, 1, 2, 2,
        2, 2, 2, 2, 3, 3, 3, 3, 3, 3]
    """
    night, label, counts = await _night(group_hub, monkeypatch, case,
                                        transient=True)
    assert night.done, night.lines[-3:]
    assert counts and counts == [0] * len(counts), (
        f"{label}'s failure count moved: {counts}")
    who = f"{GROUP_NAME} {label}"
    hops = [night.rel(t) for t, name in night.gotos if name == who]
    assert len(hops) == TRANSIENT_HOPS + 3, hops
    words = night.said(f"could not run")
    assert len(words) == TRANSIENT_HOPS, words
    assert all("not counted as a failed visit" in m and label in m
               for m in words), words
    assert night.said("consecutive") == [], night.said("consecutive")
    assert night.stored.set_aside == [], night.stored.set_aside
    assert (night.stored.owed(), night.stored.status) == (0, "complete")


@pytest.mark.parametrize("case", sorted(CASES))
async def test_without_the_key_the_same_misses_are_counted(
        group_hub, monkeypatch, case):
    """CONTROL: the same four misses without ``solve_transient`` are the
    panel's, counted as always, and the third sets it aside: a centring
    miss at its pass's end (held there since #534, a CENTRING set-aside that
    may expire), a rotator skip at its visit (a set-aside for the night)."""
    night, label, counts = await _night(group_hub, monkeypatch, case,
                                        transient=False)
    assert night.done, night.lines[-3:]
    assert max(counts) >= 1, counts
    first = night.stored.set_aside[0]
    assert first["reason"].startswith(
        {"centring": "centring failed on 2-2 on 3 consecutive visits",
         "rotate": "the rotator did not turn the camera to the mosaic's "
                   "angle on 1-2 on 3 consecutive visits"}[case]), first
    assert first["kind"] == {"centring": CENTRING, "rotate": "deferred"}[case]
