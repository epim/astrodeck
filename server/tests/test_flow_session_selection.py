"""One rule picks a flow's session, and the card chip and Run both use it
(#189 hardening A2; spec 5.9 "Across nights", 1.2 card chip).

``GET /api/flows/{id}/progress`` (the card's chip, the CONTINUE button's copy)
and ``POST /api/flows/{id}/run`` (CONTINUE itself) each answer "which session
is this flow's work". They answered it two ways: the chip read the newest
session that was not abandoned, Run the newest of any status. The two agree
until a flow holds the session a START OVER left behind. START OVER leaves the
old session dormant and unarmed for good, so once the newer session is
abandoned the chip fell back to that old ledger while Run started fresh: the
card counted frames toward a session no button would continue, and CONTINUE's
copy would have named it.

Now there is ONE function, ``SessionStore.current_for_flow``: the newest
session of the flow by ``created_ts``, of any status, and None when that
newest one is abandoned. The chip shows what it returns. Run continues it only
when it is dormant.

THE CASES, each with the older unarmed dormant session a START OVER leaves
behind, which must never be picked up again and never written:

    newest      chip names      Run
    dormant     the newest      continues the newest
    complete    the newest      starts fresh (reopening one is I-30)
    abandoned   nothing (null)  starts fresh

THE HARNESS is ``test_flows_continue.py``'s ``rig``: the real app over ASGI on
the test's own loop and a real ``SequenceEngine`` whose imaging loop alone is
replaced. Every session here is made the way an operator makes it: night one
through Run, START OVER through Run with ``fresh``, the newer night ended by
the engine's own finalize, and an abandon through ``PATCH /api/sessions``.

MUTATIONS. Each was written over a byte backup of the file it changes
(``api/app.py`` or ``sequence/session.py``) in a copy of ``server/`` taken
from HEAD, so no other suite on the shared tree could import it; only the
named tests were run, and the copy was restored and SHA-256 compared after
every mutant. The real files were hashed before and after and never written.
The failures are quoted in the test's docstring as observed.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from astrodeck.sequence.session import session_store
from test_flows_continue import LR, _bytes, rig  # noqa: F401 (fixture)


async def _clock_past(ts: float) -> None:
    """Return once ``time.time()`` reads later than ``ts``.

    ``created_ts`` is what orders a flow's sessions, and ``time.time()`` on
    Windows (Python 3.12) ticks every 15.625 ms. Two starts inside one tick
    would tie, and the tie would be broken by file-name order, which is a
    uuid: a coin toss about which session is newest. Night one and the START
    OVER here are separated by only a few awaits, so the test waits out the
    tick rather than hoping it passed."""
    while time.time() <= ts:
        await asyncio.sleep(0.001)


async def _progress_session(rig, fid: str) -> dict | None:
    r = await rig.client.get(f"/api/flows/{fid}/progress")
    assert r.status_code == 200, r.text
    return r.json()["session"]


@pytest.mark.parametrize("newest", ["dormant", "complete", "abandoned"])
async def test_the_chip_and_run_pick_the_same_session(rig, newest):
    """The chip and Run agree, in every case the newest session can be in,
    and the session START OVER left behind is never named, never continued
    and never written.

    Before the fix the abandoned case was RED on this tree, which is the
    defect (observed):

        AssertionError: the chip named a session Run will not continue
        assert {'count_mode': 'attempts', 'id':
        '1e5f8b18f26b41a0a201edc758aaadd6', 'nights': 1, 'status':
        'dormant'} is None

    RED under mutation "progress keeps its non-abandoned filter"
    (``_flow_progress_payload`` reading ``session_store.newest_for_flow(
    flow_id, ("active", "dormant", "complete"))``, the rule it had), in the
    abandoned case only: the chip names the old ledger Run will not
    continue. Observed:

        AssertionError: the chip named a session Run will not continue
        assert {'count_mode': 'attempts', 'id':
        'd7e0a4a8358341f88e47bdddbdffd1bb', 'nights': 1, 'status':
        'dormant'} is None

    RED under mutation "run_flow picks newest dormant" (``session_store.
    newest_for_flow, flow_id, ("dormant",)`` as Run's lookup), in the
    complete and the abandoned cases: Run reopens the ledger START OVER
    left. Observed:

        [complete]
        AssertionError: Run did not do what the chip says: continued
        '4277705dae5b474a95fbe888861242e6', the chip names {'id':
        'f844010bdecb4bc88917c09bcb1c3432', 'status': 'complete', 'nights':
        1, 'count_mode': 'attempts'}
        assert '4277705dae5b474a95fbe888861242e6' == None

        [abandoned]
        AssertionError: Run did not do what the chip says: continued
        'c9b2380069f5423580378a6eca57c5e4', the chip names None
        assert 'c9b2380069f5423580378a6eca57c5e4' == None

    RED under mutation "the method falls back past an abandoned newest"
    (``current_for_flow`` asking ``newest_for_flow(flow_id, ("active",
    "dormant", "complete"))``), in the abandoned case. Both halves reopen the
    old ledger, and the chip line sees it first. Observed:

        AssertionError: the chip named a session Run will not continue
        assert {'count_mode': 'attempts', 'id':
        '64cbd94a48f645078479c098b8126e23', 'nights': 1, 'status':
        'dormant'} is None

    RED under mutation "the method returns an abandoned newest" (``if s is
    None or s.status == "abandoned":`` -> ``if s is None:``), in the
    abandoned case. Observed:

        AssertionError: the chip named a session Run will not continue
        assert {'count_mode': 'attempts', 'id':
        '418bf72035aa414ea589a86e71607333', 'nights': 1, 'status':
        'abandoned'} is None

    The dormant case is the CONTROL: every mutation above leaves it green,
    because the newest dormant session is also the newest session.
    """
    fid = await rig.save_flow(LR)
    old = await rig.night_one(fid, [0])
    await _clock_past(old.created_ts)

    r = await rig.run(fid, fresh=True)                     # START OVER
    assert r.status_code == 200, r.text
    new_id = r.json()["session"]["id"]
    assert new_id not in (None, old.id)
    assert session_store.load(old.id).auto_resume is False, (
        "premise: START OVER's start disarmed the old session, which is what "
        "leaves it dormant and unarmed for good")
    # Taken after START OVER's own disarm, the last write the old session is
    # owed. Nothing below may touch it.
    before = _bytes(old.id)

    if newest == "complete":
        rig.bank([0, 0, 0, 1, 1])
        await rig.end_night("complete")
    else:
        rig.bank([0, 1])
        await rig.end_night("incomplete")
    if newest == "abandoned":
        r = await rig.client.patch(f"/api/sessions/{new_id}",
                                   json={"status": "abandoned"})
        assert r.status_code == 200, r.text
    new = session_store.load(new_id)
    assert new.status == newest, "premise: the newer session's status"
    assert new.created_ts > old.created_ts, "premise: the newer one is newer"
    assert session_store.load(old.id).status == "dormant"

    chip = await _progress_session(rig, fid)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    start = rig.starts[-1]
    assert start.won, start
    continued = start.session_id

    # What each half must say, case by case (the table in the module
    # docstring). Together they are the agreement: Run continues the session
    # the chip names exactly when that session is dormant, and otherwise
    # starts fresh. The chip is checked first so a failure names the half
    # that broke.
    if newest == "abandoned":
        assert chip is None, "the chip named a session Run will not continue"
    else:
        assert chip is not None and (chip["id"], chip["status"]) == (
            new_id, newest), f"the chip named the wrong session: {chip}"
    expect = new_id if newest == "dormant" else None
    assert continued == expect, (
        f"Run did not do what the chip says: continued {continued!r}, the "
        f"chip names {chip}")
    assert r.json()["session"]["continued"] is (expect is not None)
    assert _bytes(old.id) == before, (
        "the session START OVER left behind was written")
