"""The mosaic group data model and its identity checks (#189 U-01, S2 T1;
advances #156; spec 3.4, 3.5, 6.13).

``TargetGroup`` is the engine's view of one TARGET block with a grid: its
members are the targets whose ``mosaic_group`` equals ``group.id``, and the
group carries what the panel loop needs that no single panel does (the visit
bound, the order, the failure budget, the angle and the skipped panels). Every
field is additive, so a plan with ``groups == []`` dumps exactly as it did
before, plus the new keys at their defaults.

``plan_identity_errors`` gains the group checks. It stays a START-PATH check
and never a pydantic validator, for the reason ``test_plan_identity.py``
gives: ``SessionStore.load_all`` skips a file that fails validation without a
word, so a validator would make every stored session holding such a plan
vanish on upgrade.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed by running it in a private copy of
``server/`` (so no other suite saw the mutant), from a byte-for-byte backup of
the file under test, with the copy's sha256 compared against the backup
afterwards.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import (ExposureStep, Instruction, SequencePlan,
                                       Target, TargetGroup,
                                       duplicate_name_warning,
                                       plan_identity_errors)
from astrodeck.sequence.session import Session, session_store


# ------------------------------------------------------------ TargetGroup

#: Spec 3.4, field by field: the value each takes when nothing sets it.
GROUP_DEFAULTS = {
    "name": "", "kind": "mosaic", "mode": "rotate", "visit_passes": 1,
    "visit_min_s": 0.0, "order": "least_complete", "require_centred": True,
    "max_failed_visits": 3, "pa_deg": None, "rotate": False,
    "angle_tolerance_deg": None, "skipped_ids": [], "geometry": {},
}


def test_a_group_carries_the_spec_fields_at_the_spec_defaults():
    """RED before ``TargetGroup`` existed (the import at the top failed):

        E   ImportError: cannot import name 'TargetGroup' from
            'astrodeck.sequence.models'

    RED under mutant "require_centred defaults False" (a group would shoot a
    panel it could not centre, the one thing 5.6 step 3 forbids by default):

        E   AssertionError: assert {'angle_toler...'mosaic', ...} ==
            {'angle_toler...'mosaic', ...}
        E     Omitting 13 identical items, use -vv to show
        E     Differing items:
        E     {'require_centred': False} != {'require_centred': True}
    """
    assert TargetGroup(id="g").model_dump() == {"id": "g", **GROUP_DEFAULTS}


def test_a_group_has_no_minted_id():
    """``id`` is the key every member's ``mosaic_group`` repeats, so a group
    whose id were minted fresh would name no member at all. Every other model
    here mints its id; this one must be given one.

    RED under mutant "mint the group id" (``id: str = Field(default_factory=
    lambda: uuid4().hex)``, the Target idiom):

        E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.
            ValidationError'>
    """
    with pytest.raises(ValidationError):
        TargetGroup()


#: Each value the model must refuse. The bounds are 3.4's.
GROUP_REFUSED = [
    pytest.param("visit_passes", 0, id="passes-0"),
    pytest.param("visit_passes", 21, id="passes-21"),
    pytest.param("visit_min_s", -1, id="min-s-negative"),
    pytest.param("visit_min_s", 10800.5, id="min-s-over-3h"),
    pytest.param("max_failed_visits", 0, id="failed-0"),
    pytest.param("max_failed_visits", 21, id="failed-21"),
    pytest.param("kind", "grid", id="kind-grid"),
    pytest.param("mode", "panel_first", id="mode-unknown"),
    pytest.param("order", "random", id="order-unknown"),
]

#: The edges the bounds must still let through; a bound tightened by one
#: step (``le`` to ``lt``) refuses one of these.
GROUP_ACCEPTED = [
    pytest.param("visit_passes", 1, id="passes-1"),
    pytest.param("visit_passes", 20, id="passes-20"),
    pytest.param("visit_min_s", 0, id="min-s-0"),
    pytest.param("visit_min_s", 10800, id="min-s-3h"),
    pytest.param("max_failed_visits", 1, id="failed-1"),
    pytest.param("max_failed_visits", 20, id="failed-20"),
    pytest.param("mode", "sequential", id="mode-sequential"),
    pytest.param("order", "setting_first", id="order-setting-first"),
    pytest.param("order", "grid", id="order-grid"),
]


@pytest.mark.parametrize("field, value", GROUP_REFUSED)
def test_a_group_refuses_values_outside_the_spec(field, value):
    """RED under mutant "visit_passes unbounded above" (``le=20`` removed), on
    ``passes-21`` only:

        E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.
            ValidationError'>

    RED under mutant "mode is any string" (``mode: str = "rotate"``), on
    ``mode-unknown`` only, with the same line.
    """
    with pytest.raises(ValidationError):
        TargetGroup(id="g", **{field: value})


@pytest.mark.parametrize("field, value", GROUP_ACCEPTED)
def test_a_group_accepts_the_edges_of_the_spec(field, value):
    """RED under mutant "visit_min_s strictly under 3 h" (``le=10800`` made
    ``lt=10800``), on ``min-s-3h`` only:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for TargetGroup
        E   visit_min_s
        E     Input should be less than 10800 [type=less_than,
              input_value=10800, input_type=int]
    """
    assert getattr(TargetGroup(id="g", **{field: value}), field) == value


def test_the_new_target_and_plan_fields_default_to_nothing():
    """A target is not a panel and waits for no group unless a compile says so,
    and a plan carries no group unless one is given (``groups == []`` is the
    byte-identical run of 3.4).

    RED under mutant "panel_row defaults 0" (0 is a real row, the first):

        E   AssertionError: assert (0, None, None, []) == (None, None, None, [])
    """
    t = Target(name="M31", ra_hours=0.7, dec_deg=41.3)
    plan = SequencePlan(targets=[t])
    assert (t.panel_row, t.panel_col, t.after_group, plan.groups) == (
        None, None, None, [])


# ------------------------------------------------------- the golden plan

#: The flow-to-plan golden (``test_a_clean_flow_is_not_ten_warnings.py``).
GOLDEN = Path(__file__).parent / "fixtures" / "flow_plan_golden" / \
    "ngc7331_quick.json"

#: sha256 of ``json.dumps(plan, sort_keys=True)`` for the golden as S1 left
#: it, before this slice re-pinned it (``GOLDEN_SHA256`` in the golden's own
#: test, which moves with this re-pin).
GOLDEN_SHA256_BEFORE_S2 = \
    "d9ce9109734a3ec4b8340e3cdd255b9325fee6d2209208b029bc1bb76100b0c3"

#: The keys S2 adds to the dump, and the defaults the golden carries them at.
S2_PLAN_KEYS = {"groups": []}
S2_TARGET_KEYS = {"panel_row": None, "panel_col": None, "after_group": None}

#: The values S3's compile moved in the golden (S3-CP: a TARGET's own
#: centring, #170; S3-W: accepted subs, Revision 2 ruling 2), each as
#: ``(S3's value, the value before S3)``. Unlike S2's, these fields existed
#: before, so they are put back rather than taken off.
S3_PLAN_VALUES = {"count_mode": ("accepted", "attempts")}
S3_TARGET_VALUES = {"center_tolerance_arcmin": (1.2, None),
                    "center_attempts": (3, None)}


def test_the_golden_moved_by_the_s2_keys_and_nothing_else():
    """THE RE-PIN, BOUNDED FROM BOTH SIDES. The golden gained the four keys at
    their defaults; with them taken off it is the S1 golden byte for byte (the
    hash), and the S1-shaped plan, read by this code, dumps as the re-pinned
    golden exactly. So the new keys are the only change to the dump, and a
    re-pin that waved through any other moved field is red here.

    RED against the golden as S1 left it, before the re-pin (with the fields
    already in the code):

        E   AssertionError: assert {'groups': '<absent>'} == {'groups': []}
        E     Differing items:
        E     {'groups': '<absent>'} != {'groups': []}

    RED under mutant "panel_row defaults 0", on the last assertion:

        E   AssertionError: assert {'apply_filte...s': None, ...} ==
            {'apply_filte...s': None, ...}
        E     Omitting 27 identical items, use -vv to show
        E     Differing items:
        E     {'targets': [{'acquisition': 'cycle', 'after_group': None,
              'autofocus_first': True, 'autofocus_skip_if_fresh': False,
              ...}]} != {'targets': [{'acquisition': 'cycle', 'after_group':
              None, 'autofocus_first': True, 'autofocus_skip_if_fresh...

    RED under mutant "one more Target key" (``panel_label: str = ""`` added
    beside the three), on the last assertion, with the same diff.

    EXTENDED IN THE INTEGRATION OF S3: S3 re-pinned the golden's count mode
    and centring (``S3_PLAN_VALUES``, ``S3_TARGET_VALUES``), so those are put
    back to their earlier values before the hash, after checking the golden
    holds exactly S3's. Every other field still reaches the S1 hash. The
    model check moved above the hash, since it compares with the golden as
    it is, S3's values included. RED under mutant "S3 moved one more field"
    (in a private scratch copy of the golden, the first target's
    ``autofocus_first`` true made false), observed:

        E   AssertionError: assert '7c3282366c48...83edcfd32ca14' ==
            'd9ce9109734a...c1bb76100b0c3'
    """
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    before = copy.deepcopy(want)
    assert {k: before.pop(k, "<absent>") for k in S2_PLAN_KEYS} == S2_PLAN_KEYS
    for target in before["targets"]:
        assert {k: target.pop(k, "<absent>") for k in S2_TARGET_KEYS} == \
            S2_TARGET_KEYS
    assert SequencePlan.model_validate(before).model_dump(mode="json") == want
    for k, (s3, earlier) in S3_PLAN_VALUES.items():
        assert before[k] == s3, f"premise: the golden holds S3's {k}"
        before[k] = earlier
    for target in before["targets"]:
        for k, (s3, earlier) in S3_TARGET_VALUES.items():
            assert target[k] == s3, f"premise: the golden holds S3's {k}"
            target[k] = earlier
    blob = json.dumps(before, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == \
        GOLDEN_SHA256_BEFORE_S2


# ------------------------------------------------------------ builders

def _target(name: str, *, tid: str | None = None, group: str | None = None,
            row: int | None = None, col: int | None = None,
            **kw) -> Target:
    """A light target whose ids derive from ``tid`` (or the name), so two
    targets that share a name still differ in every id unless a test says
    otherwise: a test on a repeated name then watches the name rule alone."""
    tid = tid or name.replace(" ", "_")
    return Target(id=f"t-{tid}", name=name, ra_hours=0.7, dec_deg=41.3,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(id=f"s-{tid}", filter="L",
                                      exposure_s=1.0, count=2)],
                  mosaic_group=group, panel_row=row, panel_col=col, **kw)


def _panels(group: str = "g1") -> list[Target]:
    return [_target("M31 1-1", group=group, row=0, col=0),
            _target("M31 1-2", group=group, row=0, col=1)]


def _plan(*targets: Target, groups: tuple[TargetGroup, ...] = (),
          rules: tuple[Instruction, ...] = ()) -> SequencePlan:
    return SequencePlan(name="mosaic", guide=False, dither_every=0,
                        meridian_flip=False, targets=list(targets),
                        groups=list(groups), instructions=list(rules))


G1 = TargetGroup(id="g1", name="M31")


# ------------------------------------------------- the identity checks

def test_control_a_clean_group_plan_has_nothing_to_say():
    """CONTROL: a group with two members and a follower that waits for it by
    its id. Nothing to refuse and nothing to warn about."""
    plan = _plan(*_panels(), _target("NGC 7331", after_group="g1"),
                 groups=(G1,))
    assert plan_identity_errors(plan) == []
    assert duplicate_name_warning(plan) is None


def test_a_group_with_no_members_is_refused():
    """A group whose id no target's ``mosaic_group`` repeats: every panel
    skipped, or a hand edit. The group driver would run a mosaic of nothing.

    RED under mutant "drop the no-members check" (a memberless group starts):

        E   assert [] == ["group 'g2' ...ot a mosaic"]
        E     Right contains one more item: "group 'g2' ('M33') has no
              members: no target's mosaic_group names it, so it is not a
              mosaic"

    RED under mutant "any grouped target is a member" (membership read as
    ``any(t.mosaic_group for t in targets)``: g1's panels carry one), with the
    same diff.
    """
    plan = _plan(*_panels(), groups=(G1, TargetGroup(id="g2", name="M33")))
    assert plan_identity_errors(plan) == [
        "group 'g2' ('M33') has no members: no target's mosaic_group names "
        "it, so it is not a mosaic"]


def test_control_a_mosaic_group_with_no_groups_entry_is_not_refused():
    """CONTROL: today's Plan-UI mosaics set ``mosaic_group`` and have no
    ``groups`` entry, and 3.4 keeps their panel-first behaviour. Refusing
    them would strand every stored classic mosaic session.

    RED under mutant "a mosaic_group must name a group" (each target's
    ``mosaic_group`` checked against the group ids):

        E   assert ["target 'M31...o such group"] == []
        E     Left contains 2 more items, first extra item: "target 'M31 1-1'
              is in group 'M31', and the plan has no such group"
    """
    plan = _plan(*_panels(group="M31"))
    assert plan_identity_errors(plan) == []


def test_a_calibration_target_in_a_group_is_refused():
    """Darks, bias and flats skip the slew, the centring, the focus and the
    guider (``Target.calibration``), and a group member is hopped to,
    centred and angle-checked, so the two cannot both hold.

    RED under mutant "drop the calibration check":

        E   assert [] == ['calibration...mosaic panel']
        E     Right contains one more item: "calibration target 'Flats' is in
              group 'g1'; darks, bias and flats are shot where the mount is,
              and a group member is a mosaic panel"
    """
    plan = _plan(*_panels(), _target("Flats", group="g1", calibration=True),
                 groups=(G1,))
    assert plan_identity_errors(plan) == [
        "calibration target 'Flats' is in group 'g1'; darks, bias and flats "
        "are shot where the mount is, and a group member is a mosaic panel"]


def test_control_a_calibration_target_outside_every_group_is_not_refused():
    """CONTROL: a calibration target with no ``mosaic_group``, and one whose
    ``mosaic_group`` names no group (a classic plan's), are in no group.

    RED under mutant "any calibration target with a mosaic_group" (the check
    reads ``t.calibration and t.mosaic_group``), on the second target:

        E   assert ['calibration...mosaic panel'] == []
        E     Left contains one more item: "calibration target 'Darks' is in
              group 'legacy'; darks, bias and flats are shot where the mount
              is, and a group member is a mosaic panel"
    """
    plan = _plan(*_panels(), _target("Bias", calibration=True),
                 _target("Darks", group="legacy", calibration=True),
                 groups=(G1,))
    assert plan_identity_errors(plan) == []


@pytest.mark.parametrize("gate", [
    pytest.param("g9", id="unknown-id"),
    # after_group is a group ID. The group's NAME is what an operator reads,
    # and a compile that wrote it would gate on nothing.
    pytest.param("M31", id="group-name"),
])
def test_an_after_group_that_names_no_group_is_refused(gate):
    """RED under mutant "drop the after_group check", on both parameters (the
    ``unknown-id`` line):

        E   assert [] == ["target 'NGC...o such group"]
        E     Right contains one more item: "target 'NGC 7331' waits for group
              'g9', and the plan has no such group"

    RED under mutant "after_group may name a group by its name" (the gate
    compared with the group ids and the group names), on ``group-name`` only:

        E   assert [] == ["target 'NGC...o such group"]
        E     Right contains one more item: "target 'NGC 7331' waits for group
              'M31', and the plan has no such group"
    """
    plan = _plan(*_panels(), _target("NGC 7331", after_group=gate),
                 groups=(G1,))
    assert plan_identity_errors(plan) == [
        f"target 'NGC 7331' waits for group {gate!r}, and the plan has no "
        f"such group"]


def test_an_after_group_naming_a_classic_mosaic_group_is_refused():
    """A classic Plan-UI mosaic sets ``mosaic_group`` with no ``groups`` entry,
    and nothing runs it as a group, so no group state ever opens a gate on it:
    a follower waiting for it waits on nothing. ``after_group`` names a
    ``TargetGroup`` id, never a bare ``mosaic_group``.

    RED under mutant "after_group checked against any mosaic_group" (the gate
    compared with every target's ``mosaic_group`` rather than the group ids),
    which the two cases above both survive, since neither gate is any
    target's ``mosaic_group``:

        E   assert [] == ["target 'NGC...o such group"]
        E     Right contains one more item: "target 'NGC 7331' waits for group
              'M33', and the plan has no such group"
    """
    plan = _plan(*_panels(), _target("M33 1-1", group="M33"),
                 _target("NGC 7331", after_group="M33"), groups=(G1,))
    assert plan_identity_errors(plan) == [
        "target 'NGC 7331' waits for group 'M33', and the plan has no such "
        "group"]


def test_an_after_group_in_a_plan_with_no_groups_is_refused():
    """With ``groups == []`` the gate names no group by construction. The
    "empty groups is today's run" rule is about a plan with no group fields
    set, and this one sets one, so it is refused like any other gate on
    nothing, never waved through as a classic plan.

    RED under mutant "after_group checked only when the plan has groups"
    (``plan.groups and`` in front of the gate check, the shape an early
    "no groups, nothing to check" return takes), which every case above
    survives, since each carries G1:

        E   assert [] == ["target 'NGC...o such group"]
        E     Right contains one more item: "target 'NGC 7331' waits for group
              'g1', and the plan has no such group"
    """
    plan = _plan(_target("M31"), _target("NGC 7331", after_group="g1"))
    assert plan_identity_errors(plan) == [
        "target 'NGC 7331' waits for group 'g1', and the plan has no such "
        "group"]


def test_every_repeated_name_is_refused_in_a_plan_with_groups():
    """With groups, the log, the published state and the set-aside report
    speak in panel labels and target names (6.9), and a panel label is the
    block's name: two targets with one name leave no sentence that says which.
    So the rule-names-it condition does not apply: every repeat is refused,
    panels and followers alike.

    RED under mutant "names refused only when a rule names them, even with
    groups" (the S0 condition kept as it was):

        E   assert [] == ["target name...l them apart"]
        E     Right contains 2 more items, first extra item: "target name
              'M31 1-1' is used by 2 targets and the plan carries groups, so
              a panel label cannot tell them apart"
    """
    plan = _plan(*_panels(), _target("M31 1-1", tid="copy", group="g1"),
                 _target("NGC 7331", tid="a"), _target("NGC 7331", tid="b"),
                 groups=(G1,))
    sentence = ("target name {!r} is used by 2 targets and the plan carries "
                "groups, so a panel label cannot tell them apart")
    assert plan_identity_errors(plan) == [sentence.format("M31 1-1"),
                                          sentence.format("NGC 7331")]


def test_control_without_groups_a_repeated_name_is_still_a_warning():
    """CONTROL: the same repeat in a plan with no ``groups`` entry is the
    classic Plan's case, and stays a warning, even beside a classic mosaic
    whose targets carry a ``mosaic_group``: "carries groups" means
    ``plan.groups``.

    RED under mutant "groups read as any mosaic_group" (``any(t.mosaic_group
    for t in targets)`` in place of ``plan.groups``):

        E   assert ["target name...l them apart"] == []
        E     Left contains one more item: "target name 'NGC 7331' is used by
              2 targets and the plan carries groups, so a panel label cannot
              tell them apart"
    """
    plan = _plan(*_panels(group="M31"), _target("NGC 7331", tid="a"),
                 _target("NGC 7331", tid="b"))
    assert plan_identity_errors(plan) == []
    assert "'NGC 7331' x2" in (duplicate_name_warning(plan) or "")


def test_a_name_refused_for_groups_is_not_also_a_warning():
    """The warning is for the repeats the check lets start. A name it refuses
    is not offered as a warning as well.

    RED under mutant "the warning ignores groups" (its ``if plan.groups:
    return None`` removed):

        E   assert "targets share a name ('NGC 7331' x2); they run and count
            separately, but an instruction could not name just one of them"
            is None
    """
    plan = _plan(*_panels(), _target("NGC 7331", tid="a"),
                 _target("NGC 7331", tid="b"), groups=(G1,))
    assert plan_identity_errors(plan)
    assert duplicate_name_warning(plan) is None


def test_a_name_a_rule_names_keeps_its_sentence_in_a_plan_with_groups():
    """One sentence per repeated name. A rule naming it is the sharper reason
    (``test_plan_identity.py`` pins that sentence), so it is the one given.

    RED under mutant "both sentences for one name" (the rule branch and the
    group branch as two ``if``s):

        E   assert 2 == 1
        E    +  where 2 = len(["target name 'NGC 7331' is used by 2 targets
              and an instruction names it, so the rule cannot tell them
              apart", "target name 'NGC 7331' is used by 2 targets and the
              plan carries groups, so a panel label cannot tell them ...
    """
    plan = _plan(*_panels(), _target("NGC 7331", tid="a"),
                 _target("NGC 7331", tid="b"), groups=(G1,),
                 rules=(Instruction(trigger="on_target_complete",
                                    action="run_target",
                                    target_arg="NGC 7331"),))
    errors = plan_identity_errors(plan)
    assert len(errors) == 1
    assert "an instruction names it" in errors[0]


def test_every_group_problem_comes_back_in_one_answer():
    """An operator fixing a plan one refusal at a time pays a round trip per
    problem, so the group checks report together, after the id checks and
    before the names.

    RED under mutant "return after the group checks" (``if errors: return
    errors`` after the after_group loop):

        E   assert 3 == 4
        E    +  where 3 = len(["group 'g2' has no members: no target's
              mosaic_group names it, so it is not a mosaic", "calibration
              target 'Flats' is...nt is, and a group member is a mosaic
              panel", "target 'NGC 7331' waits for group 'g9', and the plan...
    """
    plan = _plan(*_panels(), _target("Flats", group="g1", calibration=True),
                 _target("NGC 7331", tid="a", after_group="g9"),
                 _target("NGC 7331", tid="b"),
                 groups=(G1, TargetGroup(id="g2")))
    errors = plan_identity_errors(plan)
    assert len(errors) == 4
    assert errors[0].startswith("group 'g2' has no members")
    assert errors[1].startswith("calibration target 'Flats'")
    assert errors[2].startswith("target 'NGC 7331' waits for group 'g9'")
    assert errors[3].startswith("target name 'NGC 7331'")


# ------------------------------------------ not a validator: the store

def test_a_plan_the_group_checks_refuse_still_constructs():
    """The refused plan must CONSTRUCT and round-trip, or a stored session
    holding one cannot be read (the store test below).

    RED under mutant "as a pydantic model validator" (a ``@model_validator(
    mode="after")`` on ``SequencePlan`` raising ``ValueError`` on
    ``plan_identity_errors``), at construction:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for SequencePlan
        E     Value error, group 'g2' has no members: no target's
              mosaic_group names it, so it is not a mosaic [type=value_error,
              input_value={'name': 'mosaic', 'guide...})], 'instructions':
              []}, input_type=dict]
    """
    plan = _plan(*_panels(), groups=(G1, TargetGroup(id="g2")))
    again = SequencePlan.model_validate(plan.model_dump(mode="json"))
    assert [g.id for g in again.groups] == ["g1", "g2"]


def _write_raw_group_session(status: str) -> str:
    """A session holding the plan every start path refuses: a memberless
    group, a calibration member, a gate naming no group, a repeated name.
    Written as raw JSON derived from a clean session, so the test never asks
    the model to accept the bad plan to write it."""
    clean = Session(name="old mosaic", created_ts=1.0, status=status,
                    plan=_plan(*_panels(), groups=(G1,)))
    raw = clean.model_dump(mode="json")
    plan = raw["plan"]
    plan["groups"].append(TargetGroup(id="g2").model_dump(mode="json"))
    extra = [_target("Flats", group="g1", calibration=True),
             _target("NGC 7331", tid="a", after_group="g9"),
             _target("NGC 7331", tid="b")]
    plan["targets"].extend(t.model_dump(mode="json") for t in extra)
    write_json_atomic(session_store._path(clean.id), raw, backup=False)
    return clean.id


def test_a_stored_session_holding_a_refused_group_plan_is_still_listed(
        tmp_path, monkeypatch):
    """``load_all`` still finds it, and ``active`` still returns it while it
    is the running session; and it is exactly the plan the start paths refuse,
    for all four group reasons.

    RED under mutant "as a pydantic model validator" (the session vanishes
    from the store, silently):

        E   AssertionError: the session vanished from load_all
        E   assert '6670180c00a84e06b3b0f17e2e20745d' in []

    The same mutant turns eleven more tests in this file red, the golden among
    them: its minted ids are blanked, so its seven steps share the id ''.
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    sid = _write_raw_group_session("active")
    assert sid in [s.id for s in session_store.load_all()], (
        "the session vanished from load_all")
    live = session_store.active()
    assert live is not None and live.id == sid, "active() lost it"
    assert len(plan_identity_errors(live.plan)) == 4


# ------------------------------------------------------------ the route

@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real app with the camera, the sun check and ``engine.start``
    stubbed, as ``test_plan_identity.py`` isolates it: the only thing between
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
        monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        starts: list[SequencePlan] = []
        monkeypatch.setattr(app_module.engine, "start",
                            lambda plan, **kw: starts.append(plan))
        c.starts = starts
        yield c


def test_the_start_route_refuses_a_memberless_group(api):
    """End to end: ``groups`` survives the start body (``StartSequenceBody``
    is the plan plus ``force``, rebuilt into a plain plan), and the refusal
    reaches the route. The CONTROL goes first and must start, so the refusal
    is watched on a request that could have reached ``engine.start``.

    RED under mutant "drop the no-members check":

        E   AssertionError: {"started":true,"frames":4}
        E   assert 200 == 422
    """
    ok = api.post("/api/sequence/start",
                  json=_plan(*_panels(), groups=(G1,)).model_dump(mode="json"))
    assert ok.status_code == 200, ok.text
    assert [len(p.groups) for p in api.starts] == [1]

    bad = _plan(*_panels(), groups=(G1, TargetGroup(id="g2")))
    r = api.post("/api/sequence/start", json=bad.model_dump(mode="json"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "plan_identity"
    assert "group 'g2' has no members" in r.json()["detail"]["detail"]
    assert len(api.starts) == 1, "the refused plan reached engine.start"
