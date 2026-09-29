"""CONTINUE exempts a skipped panel from the dropped-steps refusal (#189 S1
item 8, the server half built in S2; spec 5.9 "Other cases" row 1, 2.5, 3.3,
3.4).

Skipping a panel is not a change of identity (spec 2.5): ``skip`` is left out
of the geometry key (3.3), so the compile drops the panel from its targets
and names its deterministic target id in its group's
``TargetGroup.skipped_ids`` (3.4). The ledger counts frames by step id alone,
so re-enabling the panel compiles the same step ids and its frames count
again. CONTINUE's dropped-steps refusal (409 ``dropped_steps``) exists to
stop frames being let go of silently; a skipped panel lets go of nothing,
so ``plan_replace_report`` lists its steps as ``skipped``, apart from
``dropped``, and CONTINUE does not ask.

S1 built the refusal with no exemption because S1 had no groups; S2 brings
``skipped_ids`` and this reader; the ``skip`` param that fills it from the
TARGET block lands in S3. So the route half here drives
``POST /api/flows/{id}/run`` with the compile monkeypatched
(``app_module.to_sequence_plan``) to return a two-panel mosaic whose group
skips the panel the test names: the flow's own graph is compiled as ever,
and only the plan it expands to is the test's.

THE HARNESS is ``test_flows_continue.py``'s (the real app over ASGI on the
test's own loop, a real ``SequenceEngine`` whose imaging loop alone is
replaced, the ledger written through the engine's ``_record_session_frame``
and each night ended through its ``_finalize_report``).

MUTATIONS. Each was applied to a byte-for-byte copy of
``flows/continuation.py`` in a private copy of ``server/`` under the
session scratchpad, only this file was run there, and the copy was restored
and SHA-256 compared after every mutant; the shared tree was never written.
Failures are quoted from ``--tb=short``, wrapped to fit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import astrodeck.api.app as app_module
from astrodeck.flows import identity
from astrodeck.flows.continuation import ReplaceReport, plan_replace_report
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_flows_continue import LR, rig  # noqa: F401 (fixture)

#: Where the mosaic is and what it is called. Typed numbers, not a site: the
#: default site never blocks the horizon check, and the Sun check is stubbed.
RA, DEC = 5.588, -5.391
NAME = "M42"
#: The two panels, 0-based (row, col): labelled "1-1" and "1-2".
P11, P12 = (0, 0), (0, 1)


# ------------------------------------------------------------------ plans

def _step(target_id: str, stage: str, filt: str, exposure: float,
          count: int) -> ExposureStep:
    """One step with the id ``flows/identity`` mints for it, so two compiles
    of the same recipe on the same panel name the same step."""
    return ExposureStep(
        id=identity.step_id(target_id, stage, frame_type="Light", filter=filt,
                            exposure_s=exposure, gain=100, binning=1),
        filter=filt, exposure_s=exposure, gain=100, binning=1, count=count)


def _mosaic(flow_id: str, *, skip=(), r_exposure: float = 0.05,
            cool_to: float | None = None) -> SequencePlan:
    """A 1x2 mosaic of ``NAME`` as S3's compile will expand it (spec 3.3): one
    ``TargetGroup``, one target per panel NOT in ``skip`` with the ids
    ``identity`` mints, and the skipped panels' target ids in the group's
    ``skipped_ids``. Every panel shoots L (3) and R (2); ``r_exposure``
    changes R's recipe, which changes R's step id and nothing else (#77)."""
    gid = identity.group_id(flow_id, "t", "anchor")
    targets: list[Target] = []
    skipped: list[str] = []
    for row, col in (P11, P12):
        tid = identity.target_id(gid, row, col)
        if (row, col) in skip:
            skipped.append(tid)
            continue
        targets.append(Target(
            id=tid, name=f"{NAME} {row + 1}-{col + 1}", ra_hours=RA,
            dec_deg=DEC + 0.5 * col, center=False, autofocus_first=False,
            mosaic_group=gid, panel_row=row, panel_col=col,
            steps=[_step(tid, "c1", "L", 0.05, 3),
                   _step(tid, "c2", "R", r_exposure, 2)]))
    return SequencePlan(name="continue me", targets=targets, cool_to=cool_to,
                        groups=[TargetGroup(id=gid, name=NAME,
                                            skipped_ids=skipped)])


def _ids(flow_id: str, panel: tuple[int, int]) -> tuple[str, list[str]]:
    """``(target id, [L step id, R step id])`` of one panel, as ``_mosaic``
    names them whether or not it is skipped."""
    t = next(t for t in _mosaic(flow_id).targets
             if (t.panel_row, t.panel_col) == panel)
    return t.id, [st.id for st in t.steps]


def _session(plan: SequencePlan, banked: dict[str, int]) -> Session:
    """A dormant session of ``plan`` holding ``banked[step_id]`` frames on
    each step named, each on the target that owns the step."""
    owner = {st.id: t.id for t in plan.targets for st in t.steps}
    s = Session(status="dormant", plan=plan.model_copy(deep=True))
    for sid, n in banked.items():
        s.frames += [SessionFrame(target_id=owner[sid], step_id=sid)
                     for _ in range(n)]
    return s


def _ledger(s: Session) -> list[tuple[str, str, str]]:
    return [(f.id, f.target_id, f.step_id) for f in s.frames]


# ================================================================ the pure half

FLOW = "flow-skip"


class TestTheReportExemptsASkippedPanel:
    def _banked(self) -> tuple[Session, dict]:
        """Both panels shot: 1-1 holds 1 L and 2 R, 1-2 holds 3 L and 1 R."""
        _t11, (l11, r11) = _ids(FLOW, P11)
        _t12, (l12, r12) = _ids(FLOW, P12)
        s = _session(_mosaic(FLOW), {l11: 1, r11: 2, l12: 3, r12: 1})
        return s, {"l11": l11, "r11": r11, "l12": l12, "r12": r12}

    def test_a_skipped_panels_steps_are_listed_apart_and_not_dropped(self):
        """1-2 skipped: its two steps, which hold 4 frames, are ``skipped``,
        not ``dropped``, and 1-1's are kept.

        RED under mutant "skipped panels count as dropped"
        (``dropped = sorted(lost - on_skipped)`` -> ``dropped =
        sorted(lost)``), and the same under mutant "remove the read"
        (``skipped_targets = set()``):

            AssertionError: a skipped panel's steps are not dropped (spec 5.9)
            assert ['450b081031a...c8355e74c2af'] == []
              Left contains 2 more items, first extra item:
              '450b081031a759e2bec08598c27c5e71'
              Use -v to get more diff
        """
        s, ids = self._banked()
        r = plan_replace_report(s, _mosaic(FLOW, skip=[P12]))
        assert r.dropped == [], "a skipped panel's steps are not dropped " \
                                "(spec 5.9)"
        assert r.dropped_frames == 0
        assert r.skipped == sorted([ids["l12"], ids["r12"]])
        assert r.skipped_frames == 4
        assert r.kept == sorted([ids["l11"], ids["r11"]])
        assert r.new == []

    def test_every_group_of_the_new_plan_is_read(self):
        """``skipped_ids`` is read from every group, not the first: a second
        block ahead of the mosaic in ``groups`` must not hide its skip.

        RED under mutant "only the first group" (``for g in new_plan.groups``
        -> ``for g in new_plan.groups[:1]``), the only test here it turns:

            AssertionError: assert ['450b081031a...c8355e74c2af'] == []
              Left contains 2 more items, first extra item:
              '450b081031a759e2bec08598c27c5e71'
              Use -v to get more diff
        """
        s, ids = self._banked()
        new = _mosaic(FLOW, skip=[P12])
        other = TargetGroup(id=identity.group_id(FLOW, "t2", "anchor"),
                            name="M43")
        new.groups.insert(0, other)
        r = plan_replace_report(s, new)
        assert r.dropped == []
        assert r.skipped == sorted([ids["l12"], ids["r12"]])

    def test_re_enabling_brings_the_same_step_ids_back_kept(self):
        """The real sequence of two CONTINUEs: the skip is continued, which
        replaces the session's plan with the skipped one (as
        ``_continue_flow_session`` does), and the panel is re-enabled on a
        later night. Its steps come back under the same ids, KEPT, with the
        ledger untouched and its counts back where they were.

        A step of the re-enabled panel that never banked a frame is ``new``,
        as it would be on any night: it starts from zero, and nothing in the
        session remembers it once the plan that listed it was replaced. Here
        1-2's R is given a frame so both of its steps are held, and the
        frameless case is the control below.

        RED under mutant "a returning step is new" (``kept =
        sorted(new_ids & (old_ids | with_frames))`` -> ``sorted(new_ids &
        old_ids)`` and ``new = sorted(new_ids - old_ids - with_frames)`` ->
        ``sorted(new_ids - old_ids)``):

            AssertionError: re-enabled, the panel's steps are kept
            assert ['442c56e8068...9712428a5596'] ==
            ['442c56e8068...9712428a5596']
              At index 1 diff: 'f19b97dc9c855036998a9712428a5596' !=
              '450b081031a759e2bec08598c27c5e71'
              Right contains 2 more items, first extra item:
              'e856e5b2301b58b498afc8355e74c2af'
              Use -v to get more diff
        """
        s, ids = self._banked()
        before = _ledger(s)
        counts_before = s._counts()
        owed_before = s.owed()
        s.plan = _mosaic(FLOW, skip=[P12])        # night two, 1-2 skipped
        assert s.owed() < owed_before, (
            "premise: while skipped, the panel's steps are not in the plan")
        r = plan_replace_report(s, _mosaic(FLOW))  # night three, re-enabled
        assert r.kept == sorted(ids.values()), (
            "re-enabled, the panel's steps are kept")
        assert r.new == [] and r.dropped == [] and r.skipped == []
        assert _ledger(s) == before, "a report never edits the ledger"
        s.plan = _mosaic(FLOW)
        assert s._counts() == counts_before
        assert s.owed() == owed_before, "its counts are restored"

    def test_control_a_step_the_re_enabled_panel_never_shot_is_new(self):
        """The half of the rule above that must not overreach: a step of the
        new plan that the old plan lacks and the ledger holds nothing on is
        ``new``, while the panel's other step, which holds frames, is kept.

        RED under mutant "every step of the new plan is kept" (``kept =
        sorted(new_ids)``):

            AssertionError: assert ['442c56e8068...9712428a5596'] ==
            ['442c56e8068...9712428a5596']
              At index 1 diff: '450b081031a759e2bec08598c27c5e71' !=
              'e856e5b2301b58b498afc8355e74c2af'
              Left contains one more item: 'f19b97dc9c855036998a9712428a5596'
              Use -v to get more diff

        Red too under "a returning step is new", through the kept half
        (``Right contains one more item``): 1-2's L holds frames.
        """
        _t11, (l11, r11) = _ids(FLOW, P11)
        _t12, (l12, r12) = _ids(FLOW, P12)
        s = _session(_mosaic(FLOW), {l11: 1, l12: 2})
        s.plan = _mosaic(FLOW, skip=[P12])
        r = plan_replace_report(s, _mosaic(FLOW))
        assert r.kept == sorted([l11, r11, l12])
        assert r.new == [r12]

    def test_control_a_really_dropped_step_is_still_dropped(self):
        """The exemption is the skipped panel's, not the plan's: 1-2 skipped
        AND 1-1's R recipe changed. 1-1's old R (2 frames) is dropped and
        counted, and 1-2's steps are still apart.

        RED under mutant "a skip in the plan exempts every step" (``dropped =
        sorted(lost - on_skipped)`` -> ``dropped = [] if skipped_targets else
        sorted(lost)``):

            AssertionError: assert [] == ['f19b97dc9c8...9712428a5596']
              Right contains one more item: 'f19b97dc9c855036998a9712428a5596'
              Use -v to get more diff

        Red too under "skipped panels count as dropped" and "remove the
        read", which drop 1-2's steps with 1-1's R (``At index 0 diff``).
        """
        s, ids = self._banked()
        r = plan_replace_report(s, _mosaic(FLOW, skip=[P12], r_exposure=0.07))
        assert r.dropped == [ids["r11"]]
        assert r.dropped_frames == 2
        assert r.skipped == sorted([ids["l12"], ids["r12"]])
        assert r.skipped_frames == 4

    def test_a_continued_skip_keeps_the_panels_frames_in_view(self):
        """A skip continued, then the panel re-enabled on the next night with
        R's recipe changed on every panel. The plan the skip was continued
        with lists none of 1-2's steps, and 1-1's R never banked a frame, so
        the one frame this lets go of is 1-2's R. It is dropped and counted,
        so CONTINUE refuses rather than let it go without a word, and 1-2's
        L, whose recipe held, comes back kept.

        RED under mutant "a continued skip forgets the panel" (``parked =
        {f.step_id for f in session.frames if f.target_id in held_back}``
        -> ``parked: set[str] = set()``), #282, run by the T12 verifier in a
        private copy of ``server/``:

            AssertionError: a skipped panel's frames stay in the refusal's
            sight
            assert [] == ['450b081031a...8598c27c5e71']
              Right contains one more item:
              '450b081031a759e2bec08598c27c5e71'
              Use -v to get more diff

        Red too under "a returning step is new" (see above), at ``l12 in
        r.kept``: 1-2's L is on no step of the old plan.
        """
        _t11, (l11, r11) = _ids(FLOW, P11)
        _t12, (l12, r12) = _ids(FLOW, P12)
        s = _session(_mosaic(FLOW), {l11: 1, l12: 3, r12: 1})
        s.plan = _mosaic(FLOW, skip=[P12])        # night two, 1-2 skipped
        r = plan_replace_report(s, _mosaic(FLOW, r_exposure=0.07))
        assert r.dropped == [r12], (
            "a skipped panel's frames stay in the refusal's sight")
        assert r.dropped_frames == 1
        assert l12 in r.kept and r.skipped == []

    def test_a_panel_skipped_two_nights_running_is_still_exempt(self):
        """Skipped on night two and again on night three, nothing else
        changed: still nothing dropped, and the panel's steps are listed
        apart, found through its frames, since neither plan lists them.

        RED under mutant "the skip is read off the plan's targets alone"
        (``on_skipped = ({st.id for t in session.plan.targets ...} | {f.step_id
        for f in session.frames if f.target_id in skipped_targets})`` ->
        the first set alone), run by the T12 verifier in a private copy:

            AssertionError: a panel skipped again is not dropped
            assert ['450b081031a...c8355e74c2af'] == []
              Left contains 2 more items, first extra item:
              '450b081031a759e2bec08598c27c5e71'
              Use -v to get more diff

        Red too under "a continued skip forgets the panel", at ``skipped``
        (``Right contains 2 more items``): nothing is found to list apart;
        and under "skipped panels count as dropped" and "remove the read",
        at ``dropped``, as above.
        """
        s, ids = self._banked()
        s.plan = _mosaic(FLOW, skip=[P12])        # night two, 1-2 skipped
        r = plan_replace_report(s, _mosaic(FLOW, skip=[P12]))  # and again
        assert r.dropped == [], "a panel skipped again is not dropped"
        assert r.dropped_frames == 0
        assert r.skipped == sorted([ids["l12"], ids["r12"]])
        assert r.skipped_frames == 4
        assert r.kept == sorted([ids["l11"], ids["r11"]]) and r.new == []

    def test_control_no_group_and_no_skip_is_the_s1_report(self):
        """With no groups, or a group that skips nothing, the report is the
        one S1 gave: a panel missing with no skip is dropped, and
        ``skipped`` is empty. PATCH's ``merge`` keeps its three keys. A
        control: green on the code and under every mutant named in this
        file."""
        s, ids = self._banked()
        gone = _mosaic(FLOW, skip=[P12])
        gone.groups[0].skipped_ids = []           # missing, not skipped
        r = plan_replace_report(s, gone)
        assert r.dropped == sorted([ids["l12"], ids["r12"]])
        assert r.dropped_frames == 4
        assert r.skipped == [] and r.skipped_frames == 0
        bare = gone.model_copy(update={"groups": []})
        assert plan_replace_report(s, bare) == r
        assert set(r.merge()) == {"kept", "new", "dropped"}
        assert isinstance(r, ReplaceReport)


# ============================================================== the route half

@dataclass
class _Compile:
    """Stands in for ``to_sequence_plan`` in ``api/app.py``: returns the
    mosaic with the panels ``skip`` names skipped, and R at ``r_exposure``.
    The test changes both between nights, as S3's ``skip`` param will."""
    skip: list = field(default_factory=list)
    r_exposure: float = 0.05
    calls: int = 0

    def __call__(self, compiled, graph, *, flow_id="", cool_to=None, **_kw):
        self.calls += 1
        return _mosaic(flow_id, skip=self.skip, r_exposure=self.r_exposure,
                       cool_to=cool_to), []


@pytest.fixture
def mosaic(rig, monkeypatch):
    fake = _Compile()
    monkeypatch.setattr(app_module, "to_sequence_plan", fake)
    return fake


def _bank(rig, panel: tuple[int, int], steps) -> None:
    """One frame per entry of ``steps`` (0 for L, 1 for R) on ``panel`` of
    the live run, through the engine's own ledger write."""
    t = next(t for t in rig.engine.plan.targets
             if (t.panel_row, t.panel_col) == panel)
    for i in steps:
        sf = rig.engine._record_session_frame(t, t.steps[i], {},
                                              auto_accepted=True)
        assert sf is not None, "the ledger write banked nothing"


async def _night_one(rig, mosaic) -> tuple[str, str]:
    """Night one through the route: both panels, 1 L and 2 R on 1-1, 3 L
    and 1 R on 1-2. Returns (flow id, session id)."""
    fid = await rig.save_flow(LR)
    r = await rig.run(fid)
    assert r.status_code == 200, r.text
    assert r.json()["session"]["continued"] is False
    assert mosaic.calls == 1, "premise: the route compiled through the fake"
    _bank(rig, P11, (0, 1, 1))
    _bank(rig, P12, (0, 0, 0, 1))
    sid = rig.engine._session.id
    await rig.end_night()
    assert session_store.load(sid).status == "dormant"
    return fid, sid


class TestContinueWithAPanelSkipped:
    async def test_skipping_a_panel_with_frames_does_not_409(self, rig,
                                                             mosaic):
        """Spec 8, S1's test line carried to S2: skipping a panel with banked
        frames and pressing CONTINUE does not 409, and re-enabling it restores
        its counts. Night two skips 1-2 and continues the same session with
        nothing dropped; the file keeps 1-2's four frames. Night three
        re-enables it: the same four step ids are kept, none new, and the
        ledger counts 1-2's frames again.

        RED under mutant "skipped panels count as dropped" (``dropped =
        sorted(lost - on_skipped)`` -> ``dropped = sorted(lost)``), and the
        same under mutant "remove the read":

            AssertionError: {"detail":{"code":"dropped_steps","detail":"4 subs
            belong to steps this flow no longer has; they stay on
            disk","dropped_frames":4,
            "session_id":"2f146ed747114ad1ad7b035cb90b7280"}}
            assert 409 == 200
             +  where 409 = <Response [409 Conflict]>.status_code

        RED under mutant "a returning step is new" (see the pure half):

            AssertionError: re-enabled, the panel's steps come back kept
            assert {'continued':...kept': 2, ...} ==
            {'continued':...kept': 4, ...}
              Omitting 4 identical items, use -vv to show
              Differing items:
              {'kept': 2} != {'kept': 4}
              {'new': 2} != {'new': 0}
              Use -v to get more diff

        DELIBERATE PIN CHANGE (S7 integration, #430, S7 orchestrator ruling
        7): the three runs used to start on the real clock, seconds apart,
        and the answers pinned ``"night": 2`` and ``3`` as the run count.
        CONTINUE's ``night`` is the observing night since S7, so the
        unpinned case went red with ``{'night': 1} != {'night': 2}``. Each
        night now starts on its own evening (``Rig.on_night``), which is
        what "night two" and "night three" say.

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
        fid, sid = await _night_one(rig, mosaic)
        t12, (l12, r12) = _ids(fid, P12)
        banked = session_store.load(sid)._counts()
        assert (banked[l12], banked[r12]) == (3, 1), (
            "premise: 1-2 holds frames")

        mosaic.skip = [P12]                                  # night two
        rig.on_night(2)
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["session"] == {
            "id": sid, "night": 2, "continued": True, "kept": 2, "new": 0,
            "dropped": 0}
        assert [t.id for t in rig.engine.plan.targets] == [_ids(fid, P11)[0]]
        assert rig.engine.plan.groups[0].skipped_ids == [t12]
        await rig.end_night()
        held = session_store.load(sid)
        assert sum(1 for f in held.frames if f.target_id == t12) == 4, (
            "the skipped panel's frames stay in the ledger")

        mosaic.skip = []                                     # night three
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

    async def test_control_a_really_dropped_step_still_409s(self, rig,
                                                            mosaic):
        """The same night two with 1-1's R recipe changed as well: that step
        held 2 frames and is really gone, so CONTINUE still refuses, and the
        count it names is those 2, not 1-2's 4 as well. Nothing is written,
        and ``accept_dropped`` then continues with the one step dropped.

        RED under mutant "a skip in the plan exempts every step" (see the
        pure half):

            AssertionError: {"started":true,
            "flow_id":"ab51b3ec3f824930a948dadbd3c46792","frames":5,
            "unmapped":[],"session":{"id":"9e5d6c41e67142f5a557e9d7a22b6165",
            "night":2,"continued":true,"kept":1,"new":1,"dropped":0}}
            assert 200 == 409
             +  where 200 = <Response [200 OK]>.status_code

        RED under "skipped panels count as dropped" and "remove the read",
        which count 1-2's frames as well:

            assert 6 == 2
        """
        fid, sid = await _night_one(rig, mosaic)
        on_disk = session_store._path(sid).read_bytes()
        mosaic.skip, mosaic.r_exposure = [P12], 0.07
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps"
        assert detail["dropped_frames"] == 2
        assert session_store._path(sid).read_bytes() == on_disk
        r = await rig.run(fid, accept_dropped=True)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["dropped"] == 1

    async def test_a_continued_skip_still_refuses_on_its_frames(self, rig,
                                                                mosaic):
        """1-2 skipped on night two and again on night three, both continued
        without a question; on night four it is re-enabled with R's recipe
        changed on both panels. Both old R steps hold frames (1-1's 2, 1-2's
        1), so CONTINUE refuses and names all 3, although the plan it would
        replace, night three's, lists none of 1-2's steps. Nothing is
        written, and ``accept_dropped`` then continues with both dropped.

        RED under mutant "a continued skip forgets the panel" (see the pure
        half), run by the T12 verifier in a private copy of ``server/``:

            AssertionError: 1-1's R (2) and 1-2's R (1)
            assert 2 == 3

        RED under mutant "the skip is read off the plan's targets alone",
        at night three:

            AssertionError: {"detail":{"code":"dropped_steps","detail":"4 subs
            belong to steps this flow no longer has; they stay on
            disk","dropped_frames":4,
            "session_id":"337500bab80849bfab53753b38e7b532"}}
            assert 409 == 200
             +  where 409 = <Response [409 Conflict]>.status_code

        Red too under "skipped panels count as dropped" and "remove the
        read", with the same 409 at night two.
        """
        fid, sid = await _night_one(rig, mosaic)
        mosaic.skip = [P12]                                  # night two
        r = await rig.run(fid)
        assert r.status_code == 200, r.text
        await rig.end_night()
        r = await rig.run(fid)                               # night three
        assert r.status_code == 200, r.text
        assert r.json()["session"]["dropped"] == 0
        await rig.end_night()
        on_disk = session_store._path(sid).read_bytes()
        mosaic.skip, mosaic.r_exposure = [], 0.07            # night four
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "dropped_steps"
        assert detail["dropped_frames"] == 3, "1-1's R (2) and 1-2's R (1)"
        assert session_store._path(sid).read_bytes() == on_disk
        r = await rig.run(fid, accept_dropped=True)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["dropped"] == 2
