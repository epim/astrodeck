# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The group-relation shapes ``plan_identity_errors`` refuses (#307, #287;
spec 3.5, 6.13).

S2 taught the start check to grade each group field against the plan: a group
with no members, a calibration target in a group, an ``after_group`` that
names no group. It did not grade the RELATIONS between them, and five shapes
of the same class got through, each a plan that cannot run as written:

* a member whose ``after_group`` is its own group waits for itself, and the
  gate never opens;
* groups whose members wait on each other deadlock the same way, reached by
  a cycle instead of a missing id. A member's ``after_group`` holds its whole
  group (spec 1.6), so a member's own group is the edge's tail;
* two ``TargetGroup`` entries sharing one id: every lookup by id (members,
  gates, the group runs, the set-aside records) answers for whichever it
  meets first;
* a member listed in its own group's ``skipped_ids``: spec 5.9 says a
  skipped panel is not a member, and the two readings disagree about
  whether it is shot;
* a negative ``panel_row`` or ``panel_col``: ``naming.panel_label`` raises
  for it at the member's first light frame (``SequenceEngine._capture``), and
  the run ends in ``error`` there (#287).

The two gate shapes are graded as the plan states them (spec 1.6). The engine
does not read a member's gate at all yet (#330, a defect of its own), so today
such a plan runs with its gate ignored; the start check refuses the plan the
operator wrote, not the accident of what the engine skips.

Each is refused on every start path with a sentence of its own, and NEVER by
a pydantic validator or a ``Field`` bound: ``SessionStore.load_all`` skips a
file that fails validation without a word, so either would make a stored
session holding such a plan vanish, where a refused start keeps it listed and
says why (``test_plan_identity.py`` has the history).

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced. Each was observed in a private copy of
``server/`` (scratchpad ``s3-m-relations-mut-k3p8``), made from a
byte-for-byte backup of ``sequence/models.py`` and restored from it, so no
other suite saw a mutant.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup, plan_identity_errors)
from astrodeck.sequence.session import Session, session_store


# ------------------------------------------------------------ builders

def _target(name: str, *, group: str | None = None, row: int | None = None,
            col: int | None = None, **kw) -> Target:
    """A light target whose ids derive from its name, so no two targets here
    share an id or a step id unless a test says so: each test then watches
    the one rule it names and nothing else."""
    tid = name.replace(" ", "_")
    return Target(id=f"t-{tid}", name=name, ra_hours=0.7, dec_deg=41.3,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(id=f"s-{tid}", filter="L",
                                      exposure_s=1.0, count=2)],
                  mosaic_group=group, panel_row=row, panel_col=col, **kw)


def _panels(group: str, name: str, rows: int = 1, cols: int = 2,
            **kw) -> list[Target]:
    """A block's panels as the compile numbers them: 0-based, row-major,
    named "<name> <row+1>-<col+1>"."""
    return [_target(f"{name} {r + 1}-{c + 1}", group=group, row=r, col=c, **kw)
            for r in range(rows) for c in range(cols)]


def _plan(*targets: Target, groups: tuple[TargetGroup, ...] = ()
          ) -> SequencePlan:
    return SequencePlan(name="mosaic", guide=False, dither_every=0,
                        meridian_flip=False, targets=list(targets),
                        groups=list(groups))


G1 = TargetGroup(id="g1", name="M31")
G2 = TargetGroup(id="g2", name="M33")
G3 = TargetGroup(id="g3", name="NGC 7000")


def _control_plan() -> SequencePlan:
    """Everything the five rules must let through, in one plan:

    * two groups; M31's 2x2 panels numbered from row 0 col 0;
    * M33's panels waiting for M31 (a mosaic after a mosaic under "Wait for
      the mosaic": the edge g2 -> g1, and no way back);
    * a follower that is no member waiting for M33;
    * M31's ``skipped_ids`` naming the panel the compile dropped (2-2 is not
      in the plan), which is the shape a skipped panel always has;
    * M33's ``skipped_ids`` naming an M31 panel. "Its own group's" is the
      rule: another group's list does not make a target skipped;
    * a target outside every group with no grid position.
    """
    m31 = [p for p in _panels("g1", "M31", rows=2, cols=2)
           if p.name != "M31 2-2"]
    m33 = _panels("g2", "M33", after_group="g1")
    return _plan(*m31, *m33,
                 _target("NGC 7331", after_group="g2"),
                 _target("M42"),
                 groups=(G1.model_copy(update={"skipped_ids": ["t-M31_2-2"]}),
                         G2.model_copy(update={"skipped_ids": ["t-M31_1-1"]})))


# ------------------------------------------------------------ the control

def test_control_groups_a_gated_follower_and_panels_from_zero_pass():
    """CONTROL: nothing in ``_control_plan`` is refused. Each mutant below
    widens one rule past its shape. Some refusal tests go red under a few of
    them as well, from the extra sentences their own plans then earn ("row 0
    is refused" reddens every test here, since every plan numbers from 0),
    but a refusal test cannot say what a widened rule gets wrong, and this
    plan, which holds only shapes that must pass, can. Under "another
    group's skipped_ids count" it is the only test that goes red.

    RED under mutant "any gate on a group is a self-gate" (the self-gate
    rule reads ``t.mosaic_group in group_ids`` in place of ``t.after_group
    == t.mosaic_group``), on M33's panels, which wait for M31:

        E   assert ["target 'M33... never opens"] == []
        E     Left contains 2 more items, first extra item: "target 'M33 1-1'
              is in group 'g2' and waits for it: it waits for itself, so the
              gate never opens"

    RED under mutant "gates read both ways" (each gate also added as an
    edge from the group waited for back to the member's group), so one
    mosaic after another reads as a deadlock:

        E   assert ["groups 'g1'...s ever opens"] == []
        E     Left contains one more item: "groups 'g1' ('M31') and 'g2'
              ('M33') wait for each other ('g1' -> 'g2' -> 'g1'): a member's
              after_group holds its whole group, so none of their gates ever
              opens"

    RED under mutant "another group's skipped_ids count" (a target read
    against the union of every group's ``skipped_ids``), on M31 1-1, which
    only M33's list names:

        E   assert ["target 'M31... a member is"] == []
        E     Left contains one more item: "target 'M31 1-1' is in group 'g1'
              and in its skipped_ids; a skipped panel is not shot, and a
              member is"

    RED under mutant "row 0 is refused" (``at < 1`` for ``at < 0``):

        E   assert ["target 'M31... are 0-based"] == []
        E     Left contains 5 more items, first extra item: "target 'M31 1-1'
              has panel row 0 and col 0; panel rows and columns are 0-based"
    """
    assert plan_identity_errors(_control_plan()) == []


# ------------------------------------------------------------ the five rules

def test_a_member_that_waits_for_its_own_group_is_refused():
    """A member waits while its group has live members, and it is one of
    them, so the gate never opens. One sentence, and no cycle sentence as
    well: a self-gate is its own reason.

    RED under mutant "drop the self-gate rule" (the ``elif t.after_group ==
    t.mosaic_group`` branch deleted):

        E   assert [] == ["target 'M31... never opens"]
        E     Right contains one more item: "target 'M31 1-2' is in group
              'g1' and waits for it: it waits for itself, so the gate never
              opens"

    RED under mutant "a self-gate is a cycle too" (``_gate_cycles`` keeps the
    member's own group as an edge to itself):

        E   assert ["target 'M31...s ever opens"] == ["target 'M31... never
            opens"]
        E     Left contains one more item: "groups 'g1' ('M31') wait for each
              other ('g1' -> 'g1'): a member's after_group holds its whole
              group, so none of their gates ever opens"
    """
    m31 = _panels("g1", "M31")
    m31[1] = m31[1].model_copy(update={"after_group": "g1"})
    assert plan_identity_errors(_plan(*m31, groups=(G1,))) == [
        "target 'M31 1-2' is in group 'g1' and waits for it: it waits for "
        "itself, so the gate never opens"]


def test_a_mutual_pair_of_groups_is_refused():
    """M31's first panel waits for M33 and M33's first panel waits for M31.
    Each member's ``after_group`` holds its whole group, so each group waits
    for the other and neither gate ever opens.

    RED under mutant "drop the cycle rule" (the loop over ``_gate_cycles``
    reads ``[]``):

        E   assert [] == ["groups 'g1'...s ever opens"]
        E     Right contains one more item: "groups 'g1' ('M31') and 'g2'
              ('M33') wait for each other ('g1' -> 'g2' -> 'g1'): a member's
              after_group holds its whole group, so none of their gates ever
              opens"

    RED under mutant "one sentence per group in the cycle" (the ``placed``
    set ignored, so each group of a cycle opens a sentence of its own):

        E   assert ["groups 'g1'...s ever opens"] == ["groups 'g1'...s ever
            opens"]
        E     Left contains one more item: "groups 'g1' ('M31') and 'g2'
              ('M33') wait for each other ('g2' -> 'g1' -> 'g2'): a member's
              after_group holds its whole group, so none of their gates ever
              opens"
    """
    m31 = _panels("g1", "M31")
    m33 = _panels("g2", "M33")
    m31[0] = m31[0].model_copy(update={"after_group": "g2"})
    m33[0] = m33[0].model_copy(update={"after_group": "g1"})
    assert plan_identity_errors(_plan(*m31, *m33, groups=(G1, G2))) == [
        "groups 'g1' ('M31') and 'g2' ('M33') wait for each other "
        "('g1' -> 'g2' -> 'g1'): a member's after_group holds its whole "
        "group, so none of their gates ever opens"]


def test_a_longer_cycle_is_refused_once():
    """g1 -> g2 -> g3 -> g1: no two groups wait for each other directly, and
    the three still deadlock. One sentence for the one cycle, listing every
    group in it.

    RED under mutant "only a mutual pair is a cycle" (reachability taken two
    gates deep rather than to the end, which finds every mutual pair and no
    longer cycle; the pair test above survives it):

        E   assert [] == ["groups 'g1'...s ever opens"]
        E     Right contains one more item: "groups 'g1' ('M31'), 'g2' ('M33')
              and 'g3' ('NGC 7000') wait for each other ('g1' -> 'g2' -> 'g3'
              -> 'g1'): a member's after_group holds its whole group, so none
              of their gates ever opens"

    RED under mutant "one sentence per group in the cycle":

        E   assert ["groups 'g1'...s ever opens"] == ["groups 'g1'...s ever
            opens"]
        E     Left contains 2 more items, first extra item: "groups 'g1'
              ('M31'), 'g2' ('M33') and 'g3' ('NGC 7000') wait for each other
              ('g2' -> 'g3' -> 'g1' -> 'g2'): a member's after_group holds its
              whole group, so none of their gates ever opens"

    RED under mutant "gates read both ways" (see the control), where the
    way round is no longer the gates' own:

        E   assert ["groups 'g1'...s ever opens"] == ["groups 'g1'...s ever
            opens"]
        E     At index 0 diff: "groups 'g1' ('M31'), 'g2' ('M33') and 'g3'
              ('NGC 7000') wait for each other ('g1' -> 'g2' -> 'g1'): a
              member's after_group holds its whole group, so none of their
              gates ever opens" != "groups 'g1' ('M31'), 'g2' ('M33') and
              'g3' ('NGC 7000') wait for each other ('g1' -> 'g2' -> 'g3' ->
              'g1'): a member's after_group holds its whole group, so none of
              their gates ever opens"
    """
    m31 = _panels("g1", "M31", after_group="g2")
    m33 = _panels("g2", "M33", after_group="g3")
    ngc = _panels("g3", "NGC 7000", after_group="g1")
    assert plan_identity_errors(_plan(*m31, *m33, *ngc,
                                      groups=(G1, G2, G3))) == [
        "groups 'g1' ('M31'), 'g2' ('M33') and 'g3' ('NGC 7000') wait for "
        "each other ('g1' -> 'g2' -> 'g3' -> 'g1'): a member's after_group "
        "holds its whole group, so none of their gates ever opens"]


def test_two_groups_sharing_an_id_are_refused():
    """Every lookup by group id answers for whichever entry it meets first,
    so the second group's visit bound, order and angle are never read.

    RED under mutant "drop the repeated-group-id rule" (its loop reads
    ``{}.items()``):

        E   assert [] == ["group id 'g...its id alone"]
        E     Right contains one more item: "group id 'g1' is used by 2 groups
              ('M31', 'M33'), and members, gates and set-aside records find a
              group by its id alone"
    """
    plan = _plan(*_panels("g1", "M31"),
                 groups=(G1, G2.model_copy(update={"id": "g1"})))
    assert plan_identity_errors(plan) == [
        "group id 'g1' is used by 2 groups ('M31', 'M33'), and members, gates "
        "and set-aside records find a group by its id alone"]


def test_a_member_listed_in_its_own_groups_skipped_ids_is_refused():
    """A skipped panel is dropped by the compile and is no member (5.9), so a
    target that is both leaves the engine and CONTINUE disagreeing about
    whether it is shot.

    RED under mutant "drop the skipped-member rule" (its test reads
    ``if False:``):

        E   assert [] == ["target 'M31... a member is"]
        E     Right contains one more item: "target 'M31 1-2' is in group 'g1'
              and in its skipped_ids; a skipped panel is not shot, and a
              member is"
    """
    group = G1.model_copy(update={"skipped_ids": ["t-M31_1-2"]})
    plan = _plan(*_panels("g1", "M31"), groups=(group,))
    assert plan_identity_errors(plan) == [
        "target 'M31 1-2' is in group 'g1' and in its skipped_ids; a skipped "
        "panel is not shot, and a member is"]


@pytest.mark.parametrize("row, col, said", [
    pytest.param(-1, 0, "row -1", id="row"),
    pytest.param(0, -2, "col -2", id="col"),
    pytest.param(-1, -2, "row -1 and col -2", id="both"),
    # A row with no col is still a row: the refusal does not wait for the
    # pair to be complete.
    pytest.param(-3, None, "row -3", id="row-without-col"),
])
def test_a_negative_panel_row_or_col_is_refused(row, col, said):
    """``naming.panel_label`` raises for a negative index at the member's
    first light frame, and the run ends in ``error`` there (#287: "panel row
    and col are 0-based, got -1, 0"). Refused before the start instead.

    RED under mutant "drop the panel rule" (its test reads ``if False:``),
    on every parameter (the ``row`` line):

        E   assert [] == ["target 'M31... are 0-based"]
        E     Right contains one more item: "target 'M31 1-1' has panel row
              -1; panel rows and columns are 0-based"

    RED under mutant "rows only" (``panel_col`` never read), on ``col`` and
    ``both`` (the ``col`` line):

        E   assert [] == ["target 'M31... are 0-based"]
        E     Right contains one more item: "target 'M31 1-1' has panel col
              -2; panel rows and columns are 0-based"

    RED under mutant "both or neither" (refused only when row AND col are
    set), on ``row-without-col`` only:

        E   assert [] == ["target 'M31... are 0-based"]
        E     Right contains one more item: "target 'M31 1-1' has panel row
              -3; panel rows and columns are 0-based"
    """
    m31 = _panels("g1", "M31")
    m31[0] = m31[0].model_copy(update={"panel_row": row, "panel_col": col})
    assert plan_identity_errors(_plan(*m31, groups=(G1,))) == [
        f"target 'M31 1-1' has panel {said}; panel rows and columns are "
        f"0-based"]


def test_a_negative_panel_index_off_every_group_is_refused_too():
    """The grid position is 0-based wherever it is set, so the rule reads
    every target, not only a group's members (the docstring's "member or
    not"). A non-member's index is inert in the engine today, since only a
    member's light frame is named by its panel, so every test above, which
    puts the index on a member, survives a rule narrowed to members.

    RED under mutant "panel rule for members only" (``if below and
    t.mosaic_group in group_ids:``), found by the S3-M verifier:

        E   assert [] == ["target 'M42... are 0-based"]
        E     Right contains one more item: "target 'M42' has panel row -1;
              panel rows and columns are 0-based"
    """
    plan = _plan(*_panels("g1", "M31"), _target("M42", row=-1), groups=(G1,))
    assert plan_identity_errors(plan) == [
        "target 'M42' has panel row -1; panel rows and columns are 0-based"]


def _every_shape() -> dict:
    """One plan holding all five shapes, as the raw JSON a stored session
    would carry. Built from pieces that are each valid alone and assembled
    as dicts, so writing it never asks ``SequencePlan`` (or a ``Field``
    bound on ``panel_row``) to accept it."""
    m31 = _panels("g1", "M31")
    m31[1] = m31[1].model_copy(update={"after_group": "g1"})
    m33 = _panels("g2", "M33", after_group="g3")
    ngc = _panels("g3", "NGC 7000", after_group="g2")
    targets = [t.model_dump(mode="json") for t in (*m31, *m33, *ngc)]
    targets[0]["panel_row"] = -1
    groups = [G1.model_copy(update={"skipped_ids": ["t-M31_1-1"]}), G2, G3,
              G2.model_copy(update={"name": "M33 again"})]
    # The frame is an EMPTY plan, which every rule passes: a frame carrying a
    # group of no members would itself be refused, and under the "model
    # validator" mutant it would raise here, before the store is ever asked.
    raw = _plan().model_dump(mode="json")
    raw["targets"] = targets
    raw["groups"] = [g.model_dump(mode="json") for g in groups]
    return raw


def test_every_relation_problem_comes_back_in_one_answer():
    """All five at once, in one answer, in a fixed order: the repeated group
    id with the id checks, the skipped member with the membership checks,
    the gates together, the grid position last.

    It also stands for "the refused plan still constructs": the plan is
    validated from its raw JSON here, as the store would.

    RED under mutant "return after the first relation problem" (``if
    errors: return errors`` after the repeated-group-id rule):

        E   AssertionError: ["group id 'g2' is used by 2 groups ('M33', 'M33
            again'), and members, gates and set-aside records find a group
            by its id alone"]
        E   assert 1 == 5

    RED under mutant "a Field bound on the grid position" (see the store
    test below), at construction:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for SequencePlan
        E   targets.0.panel_row
        E     Input should be greater than or equal to 0
              [type=greater_than_equal, input_value=-1, input_type=int]

    Red as well, as ``assert 4 == 5``, under each of the five "drop the ...
    rule" mutants in this file, and as ``assert 6 == 5`` under "a self-gate
    is a cycle too" and "one sentence per group in the cycle".
    """
    errors = plan_identity_errors(SequencePlan.model_validate(_every_shape()))
    assert len(errors) == 5, errors
    assert errors[0].startswith("group id 'g2' is used by 2 groups")
    assert errors[1].startswith("target 'M31 1-1' is in group 'g1' and in "
                                "its skipped_ids")
    assert errors[2].startswith("target 'M31 1-2' is in group 'g1' and "
                                "waits for it")
    assert errors[3].startswith("groups 'g2' ('M33') and 'g3' ('NGC 7000') "
                                "wait for each other")
    assert errors[4].startswith("target 'M31 1-1' has panel row -1")


# ------------------------------------------ not a validator: the store

def test_a_stored_session_holding_such_a_plan_is_still_listed(
        tmp_path, monkeypatch):
    """A session holding the plan every start path now refuses is still
    found by ``load_all``, and by ``active`` while it is the running one, and
    it is exactly the plan the start paths refuse, for all five reasons.

    RED under mutant "model validator" (a ``@model_validator(mode="after")``
    on ``SequencePlan`` raising ``ValueError`` on ``plan_identity_errors``;
    the session vanishes, silently):

        E   AssertionError: the session vanished from load_all
        E   assert 'fecee8a0b14b4655be4d53a62e1c590c' in []

    RED under mutant "a Field bound on the grid position" (``Field(None,
    ge=0)`` on ``panel_row`` and ``panel_col``, #287's other suggestion):
    the same vanishing, from one negative row:

        E   AssertionError: the session vanished from load_all
        E   assert '54b972f9381c42dc9ec0a952701537ba' in []

    Under the first of these, a first draft of this test raised before the
    store was asked: its plan frame carried a group of no members, which the
    mutant's validator refused at construction. A test that fails for the
    wrong reason watches nothing, hence the empty frame in ``_every_shape``.
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    clean = Session(name="old mosaic", created_ts=1.0, status="active",
                    plan=_plan(*_panels("g1", "M31"), groups=(G1,)))
    raw = clean.model_dump(mode="json")
    raw["plan"] = _every_shape()
    write_json_atomic(session_store._path(clean.id), raw, backup=False)

    assert clean.id in [s.id for s in session_store.load_all()], (
        "the session vanished from load_all")
    live = session_store.active()
    assert live is not None and live.id == clean.id, "active() lost it"
    assert len(plan_identity_errors(live.plan)) == 5


# ------------------------------------------------------------ the route

@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real app with the camera, the sun check and ``engine.start``
    stubbed, as ``test_group_models.py`` isolates it: the only thing between
    a request and ``engine.start`` is the guard stack."""
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))
    app = app_module.create_app()
    with TestClient(app) as c:
        monkeypatch.setattr(app_module.hub, "_check_solar",
                            lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        starts: list[SequencePlan] = []
        monkeypatch.setattr(app_module.engine, "start",
                            lambda plan, **kw: starts.append(plan))
        c.starts = starts
        yield c


def test_the_start_route_refuses_a_self_gated_plan(api):
    """End to end: ``after_group`` survives the start body, and the refusal
    reaches the route as the 422 every identity refusal is. The CONTROL goes
    first and must start, so the refusal is watched on a request that could
    have reached ``engine.start``.

    RED under mutant "drop the self-gate rule":

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422
        E    +  where 200 = <Response [200 OK]>.status_code

    RED under mutant "row 0 is refused", on the CONTROL, which is what the
    control is for:

        E   AssertionError: {"detail":{"detail":"target 'M31 1-1' has panel
            row 0 and col 0; panel rows and columns are 0-based; target 'M31
            1-2' has panel row 0; panel rows and columns are 0-based","code":
            "plan_identity"}}
        E   assert 422 == 200
    """
    ok = api.post("/api/sequence/start",
                  json=_plan(*_panels("g1", "M31"),
                             groups=(G1,)).model_dump(mode="json"))
    assert ok.status_code == 200, ok.text
    assert len(api.starts) == 1

    m31 = _panels("g1", "M31")
    m31[1] = m31[1].model_copy(update={"after_group": "g1"})
    r = api.post("/api/sequence/start",
                 json=_plan(*m31, groups=(G1,)).model_dump(mode="json"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "plan_identity"
    assert ("target 'M31 1-2' is in group 'g1' and waits for it"
            in r.json()["detail"]["detail"])
    assert len(api.starts) == 1, "the refused plan reached engine.start"
