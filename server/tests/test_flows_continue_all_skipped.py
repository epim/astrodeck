"""CONTINUE over a mosaic whose every panel is skipped (#335; spec 1.2, 2.5,
3.3 and 5.9's dropped-steps row).

A skip is not a change of identity (2.5): skipping a panel moves its target
id into its group's ``skipped_ids``, and ``plan_replace_report`` lists the
steps its frames sit on as ``skipped``, never ``dropped``, so CONTINUE does
not refuse them. But a block whose EVERY panel is skipped shoots nothing, and
``to_plan`` leaves it out of the plan with a warn rather than emit a group of
no members, which ``plan_identity_errors`` refuses at every start path. With
no group, the plan held no trace of the skip, so CONTINUE counted the block's
frames as dropped and answered 409 "N subs belong to steps this flow no
longer has", about frames that re-enabling a panel brings straight back. The
progress route, reading the compile's skip list, showed the same frames as
held. The card and CONTINUE disagreed about one ledger.

THE FIX: the plan carries the skip of a block it leaves out, in an additive
plan-level ``SequencePlan.skipped_ids`` (default empty, and absent from the
dump while empty, so every other plan dumps byte for byte as before), and
``continuation.plan_skipped_ids`` reads it beside the groups' lists, for the
new plan and for the session's own plan alike. ``progress._orphaned`` asks
the same function, so the card and CONTINUE agree by construction.

The all-skipped block's own warn stays: it is a loss (spec 3.3, "dropped
with a warn"), so ``/run`` asks for it to be accepted first, and that
question is ``unmapped``, not ``dropped_steps``. Every run below that is
meant to go ahead passes ``accept_unmapped`` for that reason, and the first
press shows the warn is the only thing it accepts.

THE HARNESS is ``test_flows_continue.py``'s: the real app over ASGI, a real
``SequenceEngine`` whose imaging loop alone is replaced, frames banked
through the engine's own ``_record_session_frame`` and each night ended
through its ``_finalize_report``.

MUTANTS were run from byte backups in a private copy of ``server/`` under
the session scratchpad (``s4-compile-mut``), never in the shared tree; each
observed failure is quoted in the test it turned red.
"""
from __future__ import annotations

from astrodeck.flows.continuation import plan_replace_report, plan_skipped_ids
from astrodeck.flows.to_plan import losses
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup, plan_identity_errors)
from astrodeck.sequence.session import Session, session_store
from test_flows_continue import _capture, rig  # noqa: F401 (fixture)

#: The mosaic's two panels, 0-based (row, col): labelled "1-1" and "1-2".
P11, P12 = (0, 0), (0, 1)
EVERY_PANEL = "1-1, 1-2"


def _graph(*, skip: str = "", l_exposure: float = 0.05,
           r_exposure: float = 0.05) -> dict:
    """A 1x2 mosaic of M42 (typed catalogue coordinates, a fixed camera at PA
    0) shooting L on each panel, and beside it, in a lane of its own, a
    single target M78 shooting R. ``skip`` is the mosaic's own param; each
    exposure moves only its own recipe's step ids (#77)."""
    mosaic = {"id": "t", "type": "target", "x": 0, "y": 0,
              "params": {"name": "M42", "ra": "05h 35m 17s",
                         "dec": "-05 23 28", "rows": 1, "cols": 2,
                         "overlap": 25, "fovX": 1.0, "fovY": 0.7,
                         "angle": "Camera fixed at PA", "rotation": 0,
                         "skip": skip}}
    single = {"id": "s", "type": "target", "x": 0, "y": 300,
              "params": {"name": "M78", "ra": "05h 46m 46s",
                         "dec": "+00 00 50"}}
    c1 = {**_capture("c1", "L", exposure=l_exposure), "x": 200}
    c3 = {**_capture("c3", "R", exposure=r_exposure, count=2), "x": 200,
          "y": 300}
    return {"nodes": [mosaic, c1, single, c3],
            "edges": [{"from": "t", "fromPort": "target", "to": "c1",
                       "toPort": "run"},
                      {"from": "s", "fromPort": "target", "to": "c3",
                       "toPort": "run"}]}


def _target(plan, *, panel=None, name=None):
    return next(t for t in plan.targets
                if (panel is not None and (t.panel_row, t.panel_col) == panel)
                or (name is not None and t.name == name))


def _bank(rig, target, n: int) -> None:
    """``n`` frames on ``target``'s first step of the live run, through the
    engine's own ledger write."""
    for _ in range(n):
        sf = rig.engine._record_session_frame(target, target.steps[0], {},
                                              auto_accepted=True)
        assert sf is not None, "the ledger write banked nothing"


async def _put(rig, fid: str, graph: dict) -> None:
    r = await rig.client.put(f"/api/flows/{fid}", json={
        "flow": {"name": "all skipped", "graph": graph}})
    assert r.status_code == 200, r.text


async def _night_one(rig):
    """Both panels and M78; 2 L frames on panel 1-2 and 1 R frame on M78.
    Returns (flow id, session as it went dormant, ids), ``ids`` holding the
    two panels' target ids, panel 1-2's L step id and M78's R step id."""
    fid = await rig.save_flow(_graph())
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    plan = rig.engine.plan
    assert len(plan.groups) == 1 and len(plan.targets) == 3, (
        "premise: the mosaic is a group of two panels beside M78")
    p11, p12 = _target(plan, panel=P11), _target(plan, panel=P12)
    m78 = _target(plan, name="M78")
    _bank(rig, p12, 2)
    _bank(rig, m78, 1)
    sid = rig.engine._session.id
    await rig.end_night()
    one = session_store.load(sid)
    assert one.status == "dormant"
    return fid, one, {"t11": p11.id, "t12": p12.id, "l12": p12.steps[0].id,
                      "r78": m78.steps[0].id}


class TestContinuingAnAllSkippedMosaic:
    async def test_no_409_and_the_steps_are_reported_skipped(self, rig):
        """Night two: every panel skipped. The first press asks only about
        the block's own warn (``unmapped``); accepted, CONTINUE carries on
        with M78's step kept and nothing dropped, the plan names both panels
        in its ``skipped_ids``, the report lists panel 1-2's step as
        skipped, and the progress route counts nothing orphaned. Night
        three: the skip cleared, the panel's step comes back kept and its
        two frames count again.

        RED under mutant "no skip trace for a dropped block" (``to_plan``'s
        all-skipped branch keeps its ids to itself, as S3 built it: the
        ``left_out.extend(skipped_ids)`` line removed), observed at night
        two's accepted press:

            AssertionError: {"detail":{"code":"dropped_steps","detail":"2
            subs belong to steps this flow no longer has; they stay on
            disk","dropped_frames":2,"session_id":
            "ac124e4a69b94944a8f0e6238d1e0ee3"}}
            assert 409 == 200

        RED under mutant "CONTINUE reads the groups alone"
        (``plan_skipped_ids`` returns the groups' lists without the plan's
        own), observed the same way at night two (``"dropped_frames":2``,
        ``assert 409 == 200``).
        """
        fid, one, ids = await _night_one(rig)

        await _put(rig, fid, _graph(skip=EVERY_PANEL))       # night two
        asked = await rig.run(fid)
        assert asked.status_code == 409, asked.text
        detail = asked.json()["detail"]
        assert detail["code"] == "unmapped"
        assert [u["key"] for u in losses(detail["unmapped"])] == [
            "targets[M42].mosaic.skip"], "the block's warn is all it asks"

        r = await rig.run(fid, accept_unmapped=True)
        assert r.status_code == 200, r.text
        assert r.json()["session"] == {
            "id": one.id, "night": 2, "continued": True, "kept": 1,
            "new": 0, "dropped": 0}
        plan = rig.engine.plan
        assert plan.groups == [] and [t.name for t in plan.targets] == ["M78"]
        assert plan.skipped_ids == [ids["t11"], ids["t12"]]
        report = plan_replace_report(one, plan)
        assert (report.skipped, report.skipped_frames, report.dropped) == (
            [ids["l12"]], 2, [])

        progress = (await rig.client.get(f"/api/flows/{fid}/progress")).json()
        assert progress["orphaned"] == {"frames": 0, "steps": 0}
        (block,) = [b for b in progress["blocks"] if b["name"] == "M42"]
        assert [(p["target_id"], p["banked"]) for p in block["skipped"]] == [
            (ids["t11"], 0), (ids["t12"], 2)]
        await rig.end_night()

        await _put(rig, fid, _graph())                        # night three
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"] == {
            "id": one.id, "night": 3, "continued": True, "kept": 2,
            "new": 1, "dropped": 0}
        assert rig.engine._session._counts()[ids["l12"]] == 2, (
            "re-enabled, panel 1-2's frames count again")

    async def test_control_an_exposure_change_still_409s(self, rig):
        """CONTROL. The same night two with M78's recipe changed too: M78's
        old step held a frame and is really gone, so CONTINUE still refuses,
        naming that one frame and not the mosaic's two. Green on the code.

        RED under mutant "no skip trace for a dropped block", and under
        "CONTINUE reads the groups alone", observed for both:

            assert 3 == 1
        """
        fid, one, _ids = await _night_one(rig)
        on_disk = session_store._path(one.id).read_bytes()
        await _put(rig, fid, _graph(skip=EVERY_PANEL, r_exposure=0.07))
        r = await rig.run(fid, accept_unmapped=True)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps"
        assert detail["dropped_frames"] == 1
        assert session_store._path(one.id).read_bytes() == on_disk

    async def test_a_skip_once_continued_still_answers_for_its_frames(
            self, rig):
        """The session's own plan is read too (#282's rule, now for a block
        left out whole). Night two continues with every panel skipped, so
        the session's plan lists none of the mosaic's steps; night three
        re-enables the panels with L's recipe changed. Panel 1-2's two
        frames sit on the old L step, which no plan has any more, so
        CONTINUE refuses on them rather than let them go without a word.

        RED under mutant "the session's plan-level skip unread"
        (``plan_replace_report``'s ``held_back`` read from the session
        plan's groups alone), observed at night three, the frames let go of
        in silence:

            AssertionError: {"started":true,"flow_id":
            "d881c18e596e42fa809739d33109d0fa","frames":8,"unmapped":[],
            "session":{"id":"587eb4fbb510489697437ee8ad3ce5e1","night":3,
            "continued":true,"kept":1,"new":2,"dropped":0}}
            assert 200 == 409

        RED under mutant "skipped_ids never dumped" the same way: the
        session saved on night two lost the plan's skip.

        Under "no skip trace for a dropped block" and "CONTINUE reads the
        groups alone" it is red earlier, at night two's press (the 409 of
        the first test).
        """
        fid, one, _ids = await _night_one(rig)
        await _put(rig, fid, _graph(skip=EVERY_PANEL))        # night two
        r = await rig.run(fid, accept_unmapped=True)
        assert r.status_code == 200, r.text
        await rig.end_night()

        await _put(rig, fid, _graph(l_exposure=0.07))         # night three
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps"
        assert detail["dropped_frames"] == 2


# ------------------------------------------------------------- the model

def _step(filt: str) -> ExposureStep:
    return ExposureStep(filter=filt, exposure_s=60.0, count=5)


class TestThePlanLevelSkip:
    def test_absent_from_the_dump_while_empty(self):
        """ADDITIVE AND BYTE-IDENTICAL. A plan that leaves no block out dumps
        exactly as before the field existed, so every stored session and
        every plan golden reads the same; one that does carries its ids, and
        a session saved with them loads them back.

        RED under mutant "skipped_ids dumped when empty" (the serializer's
        test made ``if False:``, so an empty list stays in the dump),
        observed:

            AssertionError: assert 'skipped_ids' not in {'apply_filter_offsets':
            None, 'autofocus_every': 0, 'cloud_hold_darks': 0,
            'cool_timeout_s': None, ...}

        The plan goldens went red under it too, which is the byte-identity
        this protects: ``test_group_models.py::test_the_golden_moved_by_the_
        s2_keys_and_nothing_else`` and five cases of
        ``test_a_clean_flow_is_not_ten_warnings.py`` (``Left contains 1 more
        item: {'skipped_ids': []}`` and four moved hashes).

        RED under mutant "skipped_ids never dumped" (the serializer drops
        the key whatever it holds), observed:

            KeyError: 'skipped_ids'
        """
        assert "skipped_ids" not in SequencePlan().model_dump()
        assert "skipped_ids" not in SequencePlan().model_dump(mode="json")
        plan = SequencePlan(skipped_ids=["a", "b"])
        assert plan.model_dump()["skipped_ids"] == ["a", "b"]
        s = Session(status="dormant", plan=plan)
        back = Session.model_validate(s.model_dump(mode="json"))
        assert back.plan.skipped_ids == ["a", "b"]

    def test_read_with_the_groups_lists(self):
        """``plan_skipped_ids`` is the one reading: every group's list and
        the plan's own.

        RED under mutant "CONTINUE reads the groups alone", observed:

            AssertionError: assert {'p12'} == {'p12', 'q11', 'q12'}
              Extra items in the right set:
              'q11'
              'q12'
        """
        plan = SequencePlan(
            targets=[Target(name="M42 1-1", ra_hours=5.5, dec_deg=-5.0,
                            mosaic_group="g", steps=[_step("L")])],
            groups=[TargetGroup(id="g", skipped_ids=["p12"])],
            skipped_ids=["q11", "q12"])
        assert plan_skipped_ids(plan) == {"p12", "q11", "q12"}
        assert plan_skipped_ids(SequencePlan()) == set()

    def test_a_target_listed_as_skipped_is_refused(self):
        """A target the plan shoots and also lists in its own
        ``skipped_ids``: the engine would shoot it while CONTINUE read its
        frames as a skipped panel's, the disagreement #307 refuses for a
        group member. The compile never writes one (the ids come from
        blocks it left out whole); a hand edit can.

        RED under mutant "no refusal for a plan-level skip" (the check's
        test made ``if False:``), observed:

            assert [] == ["target 'M78... a target is"]
              Right contains one more item: "target 'M78' is in the plan's
              skipped_ids; a skipped panel is not shot, and a target is"
        """
        t = Target(name="M78", ra_hours=5.78, dec_deg=0.01,
                   steps=[_step("R")])
        plan = SequencePlan(targets=[t], skipped_ids=[t.id])
        assert plan_identity_errors(plan) == [
            "target 'M78' is in the plan's skipped_ids; a skipped panel is "
            "not shot, and a target is"]
        # Control: an id no target has is what the compile writes.
        assert plan_identity_errors(
            SequencePlan(targets=[t], skipped_ids=["gone"])) == []
