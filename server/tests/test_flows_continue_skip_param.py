"""CONTINUE with a panel skipped through the TARGET's own ``skip`` param
(#189 spec 5.9 "Other cases" row 1, 2.5, 3.3; the S1/S2 test debt; task
S3-A).

S1 wrote the test line "skipping a panel with banked frames and pressing
CONTINUE does not 409, and re-enabling the panel restores its counts". S2
built the server half (``plan_replace_report`` reads ``skipped_ids``), but
the ``skip`` param that fills ``skipped_ids`` from the TARGET block did not
exist, so ``test_continue_skipped_panels.py`` had to monkeypatch the route's
``to_sequence_plan`` into a hand-built plan. S3 lands the param, so this
file pays that debt: nothing is stood in for between the canvas and the
engine. The flow is SAVED through ``PUT /api/flows/{id}`` with ``skip``
typed as the operator types it ("1-2"), the route compiles it
(``compile.parse_skip``, ``to_plan._expand_mosaic``, ``identity``), and
CONTINUE decides on the real plan.

Skipping is not a change of identity (2.5): ``skip`` is in no key, so the
save keeps the block's anchor and lists nothing in ``reanchored``, the
un-skipped panel keeps its ids, and the skipped panel's ids move to its
group's ``skipped_ids``. The ledger counts by step id alone, so re-enabling
the panel compiles the same step ids and its frames count again.

THE HARNESS is ``test_flows_continue.py``'s: the real app over ASGI, a real
``SequenceEngine`` whose imaging loop alone is replaced, frames banked
through the engine's own ``_record_session_frame`` and each night ended
through its ``_finalize_report``.

MUTANTS were written over byte copies of the file named, in a private copy
of ``server/`` under the session scratchpad (``s3-a-routes-k7m2``); the
shared tree was never mutated.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.session import session_store
from test_flows_continue import _capture, rig  # noqa: F401 (fixture)

#: The two panels of the 1x2, 0-based (row, col): labelled "1-1" and "1-2".
P11, P12 = (0, 0), (0, 1)


def _graph(*, skip: str = "", r_exposure: float = 0.05) -> dict:
    """A 1x2 mosaic of M42 (typed catalogue coordinates, no site) for a fixed
    camera at PA 0, shooting L (3) then R (2) on every panel. ``skip`` is the
    TARGET's own param; ``r_exposure`` changes R's recipe, which changes R's
    step ids and nothing else (#77)."""
    target = {"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": "M42", "ra": "05h 35m 17s",
                         "dec": "-05 23 28", "rows": 1, "cols": 2,
                         "overlap": 25, "fovX": 1.0, "fovY": 0.7,
                         "angle": "Camera fixed at PA", "rotation": 0,
                         "skip": skip}}
    return {"nodes": [target, _capture("c1", "L"),
                      _capture("c2", "R", exposure=r_exposure, count=2)],
            "edges": [{"from": "t", "fromPort": "target", "to": "c1",
                       "toPort": "run"},
                      {"from": "c1", "fromPort": "complete", "to": "c2",
                       "toPort": "run"}]}


def _panel(plan, panel):
    return next(t for t in plan.targets
                if (t.panel_row, t.panel_col) == panel)


def _bank(rig, panel, steps) -> None:
    """One frame per entry of ``steps`` (0 for L, 1 for R) on ``panel`` of
    the live run, through the engine's own ledger write."""
    t = _panel(rig.engine.plan, panel)
    for i in steps:
        sf = rig.engine._record_session_frame(t, t.steps[i], {},
                                              auto_accepted=True)
        assert sf is not None, "the ledger write banked nothing"


async def _put(rig, fid: str, graph: dict) -> dict:
    r = await rig.client.put(f"/api/flows/{fid}", json={
        "flow": {"name": "continue me", "graph": graph}})
    assert r.status_code == 200, r.text
    return r.json()


async def _night_one(rig):
    """Night one through the route: both panels, 1 L and 2 R on 1-1, 3 L and
    1 R on 1-2. Returns (flow id, session id, {panel: (target id, [L, R]
    step ids)})."""
    fid = await rig.save_flow(_graph())
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    assert r.json()["session"]["continued"] is False
    plan = rig.engine.plan
    assert len(plan.groups) == 1 and len(plan.targets) == 2, (
        "premise: the route compiled the 1x2 as a group of two panels")
    ids = {p: (_panel(plan, p).id, [s.id for s in _panel(plan, p).steps])
           for p in (P11, P12)}
    _bank(rig, P11, (0, 1, 1))
    _bank(rig, P12, (0, 0, 0, 1))
    sid = rig.engine._session.id
    await rig.end_night()
    assert session_store.load(sid).status == "dormant"
    return fid, sid, ids


class TestSkippingAPanelThroughItsParam:
    async def test_continue_does_not_409_and_re_enabling_restores_counts(
            self, rig):
        """Night two: "1-2" typed into the TARGET's skip and saved. The save
        re-anchors nothing, the compile drops 1-2 and names its id in the
        group's ``skipped_ids``, and CONTINUE carries on with 1-1's two steps
        kept and nothing dropped; the file keeps 1-2's four frames. Night
        three: the skip cleared. The same four step ids come back kept, none
        new, and the ledger counts 1-2's frames again.

        RED under mutant "skipped counted as dropped"
        (``flows/continuation.py``: ``dropped = sorted(lost - on_skipped)``
        -> ``dropped = sorted(lost)``), observed at night two:

            AssertionError: {"detail":{"code":"dropped_steps","detail":"4 subs
            belong to steps this flow no longer has; they stay on disk",
            "dropped_frames":4,"session_id":"594c20e05db84a45b516ce155b0d6aed"}}
            assert 409 == 200
             +  where 409 = <Response [409 Conflict]>.status_code

        RED under mutant "skip is identity" (``flows/to_plan.py``,
        ``_expand_mosaic``: ``skipped_ids.append(tid)`` removed, so a skipped
        panel leaves the plan as a drop), observed the same way at night two
        (``"dropped_frames":4``, ``assert 409 == 200``).

        DELIBERATE PIN CHANGE (S7 integration, #430, S7 orchestrator ruling
        7): the three runs used to start on the real clock, seconds apart,
        and the answers pinned ``"night": 2`` and ``3`` as the run count.
        CONTINUE's ``night`` is the observing night since S7, so the
        unpinned case went red with ``{'night': 1} != {'night': 2}``. Each
        night now starts on its own evening (``Rig.on_night``).

        RED under mutant "tonight never adds a night" (``Session.night_at``
        answering ``len(nights)``, so the evening CONTINUE is pressed on is
        always one already run), observed in the integration's private
        copy:

            AssertionError: assert {'continued':...kept': 2, ...} ==
            {'continued':...kept': 2, ...}
              Differing items:
              {'night': 1} != {'night': 2}
        """
        rig.on_night(1)
        fid, sid, ids = await _night_one(rig)
        t12, (l12, r12) = ids[P12]

        saved = await _put(rig, fid, _graph(skip="1-2"))    # night two
        assert saved["reanchored"] == [], "a skip is not a re-frame (2.5)"
        rig.on_night(2)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"] == {
            "id": sid, "night": 2, "continued": True, "kept": 2, "new": 0,
            "dropped": 0}
        plan = rig.engine.plan
        assert [t.id for t in plan.targets] == [ids[P11][0]]
        assert plan.groups[0].skipped_ids == [t12]
        await rig.end_night()
        held = session_store.load(sid)
        assert sum(1 for f in held.frames if f.target_id == t12) == 4, (
            "the skipped panel's frames stay in the ledger")

        await _put(rig, fid, _graph(skip=""))               # night three
        rig.on_night(3)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"] == {
            "id": sid, "night": 3, "continued": True, "kept": 4, "new": 0,
            "dropped": 0}, "re-enabled, the panel's steps come back kept"
        live = rig.engine._session
        assert live.id == sid
        assert (live._counts()[l12], live._counts()[r12]) == (3, 1), (
            "re-enabled, its counts are restored")
        assert live.remaining()[l12] == 0 and live.remaining()[r12] == 1

    async def test_control_a_really_dropped_step_still_409s(self, rig):
        """The same night two with R's recipe changed as well: 1-1's old R
        held 2 frames and is really gone, so CONTINUE still refuses and
        names those 2, not 1-2's 4 as well. Nothing is written, and
        ``accept_dropped`` then continues with the one step dropped.

        A control for the exemption: green on the code. Under "skipped
        counted as dropped" and "skip is identity" it is red at the count,
        observed for both:

            assert 6 == 2
        """
        fid, sid, _ids = await _night_one(rig)
        on_disk = session_store._path(sid).read_bytes()
        await _put(rig, fid, _graph(skip="1-2", r_exposure=0.07))
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps"
        assert detail["dropped_frames"] == 2
        assert session_store._path(sid).read_bytes() == on_disk
        r = await rig.run(fid, accept_dropped=True)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["dropped"] == 1

    @pytest.mark.parametrize("typed", ["1-2", " 1-2 ", "1-2, 1-2", "1-2;"])
    async def test_the_skip_is_read_as_the_operator_types_it(self, rig, typed):
        """Spacing, a repeat and a trailing separator all name the one panel
        (``compile.parse_skip``), and every spelling continues the same way.
        A control on the parse the route relies on: green on the code, and
        red, for every spelling, under the two mutants above, with night
        two's 409 ``"dropped_frames":4`` quoted there."""
        fid, sid, ids = await _night_one(rig)
        await _put(rig, fid, _graph(skip=typed))
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["kept"] == 2
        assert rig.engine.plan.groups[0].skipped_ids == [ids[P12][0]]
