# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A grid with skipped panels has one fixed-camera tolerance, the doctor's
(#1175, WP 9.6).

With rotation off for the night (D-05) the engine's angle check used to
rebuild a corrected block's convergence from the MEMBERS the plan carries, so
a skipped panel's seams were not counted and the answer could differ from
``framing.angle_tolerance_deg``, which the doctor prints for the grid. The
rule is the grid's: skip is a reversible operator toggle outside the block's
identity, and a skipped panel may hold banked frames, so the layout's seams
all count. ``to_plan`` records ``framing.angle_tolerance_deg(spec)`` in the
corrected group's ``geometry["fixed_tolerance_deg"]`` and ``allowed_error``
answers it; a group stored before it existed has no such key and reads as it
always did.

Fixtures are the real compile of a made-up centre (RA 06h, Dec 75), 2.0 x
1.33 deg panels, one block, rotating by the compile (no rig facts), so each is
a corrected block. Nothing here reads a clock.

Numbers on the tree before the change (``allowed_error`` with rotation off, and
what the doctor prints):

    1x4 at 10 percent, panel 1-2 skipped: 0.4940927501622061 / 0.4720123507309091
    1x3 at 25 percent, panel 1-2 skipped: 6.363355801228608  / 4.713010839046237
    3x3 at 25 percent, panel 1-1 skipped: 4.599688990615574  / 4.599688990615574
    3x3 at 25 percent, none skipped:      4.599688990615574  / 4.599688990615574

Named mutant: ``_convergence_term_deg`` without its recorded-value branch, so
the term is rebuilt from the members again. The two skipped-grid cases of the
first test and the hop test fail with it; the 3x3 controls stay green.
"""
from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from astrodeck.catalog import framing
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.angle_check import allowed_error
from astrodeck.sequence.engine import GroupSetAside, SequenceEngine
from test_w2_7_allowed_error import CENTRE, _e, _n, _spec
from test_w5_1b2_chain_phases import _rounded

KEY = "fixed_tolerance_deg"
SINCE = 100.0


def _compiled(*, dec: str = "+75 00 00", rows: int = 1, cols: int = 4,
              overlap: int = 10, skip: str = "",
              angle: str = "Rotate to PA", rotation: float = 0):
    """The plan the real compile makes for one framed block."""
    params = {**CENTRE, "dec": dec, "fovX": 2.0, "fovY": 1.33, "rows": rows,
              "cols": cols, "overlap": overlap, "angle": angle,
              "rotation": rotation, "skip": skip}
    graph = FlowGraph(
        nodes=[_n("d", "dusk"), _n("t", "target", x=100, **params),
               _n("s0", "cycle", x=200)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s0", "run"),
               _e("s0", "pass", "t", "next")])
    plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                               flow_id="flow-w9-6", rig=None)
    return plan


def _doctor(*, dec: float, rows: int, cols: int, overlap: int) -> float:
    return framing.angle_tolerance_deg(
        _spec(dec, rows=rows, cols=cols, overlap=overlap / 100.0))


#: (rows, cols, overlap percent, skip text, what the engine answered before)
GRIDS = [
    pytest.param(1, 4, 10, "1-2", 0.4940927501622061, id="1x4-skip-1-2"),
    pytest.param(1, 3, 25, "1-2", 6.363355801228608, id="1x3-skip-middle"),
    pytest.param(3, 3, 25, "1-1", 4.599688990615574, id="3x3-skip-corner"),
    pytest.param(3, 3, 25, "", 4.599688990615574, id="3x3-whole"),
    pytest.param(1, 4, 10, "", 0.4720123507309091, id="1x4-whole"),
]


class TestTheEngineEnforcesTheDoctorsNumber:
    @pytest.mark.parametrize("rows,cols,overlap,skip,_before", GRIDS)
    def test_a_skipped_grid_is_allowed_what_the_doctor_prints(
            self, rows, cols, overlap, skip, _before):
        """Rotation off: ``allowed_error`` is ``framing.angle_tolerance_deg``
        of the whole grid, skipped panels counted as tiles of it.

        Mutant "term rebuilt from the members": the 1x4 answers 0.4941 where
        0.4720 is due and the 1x3 answers 6.3634 where 4.7130 is due; the
        3x3 cases and the whole 1x4 stay green.
        """
        plan = _compiled(rows=rows, cols=cols, overlap=overlap, skip=skip)
        (group,) = plan.groups
        assert group.rotate and len(plan.targets) == rows * cols - (
            1 if skip else 0)
        want = _doctor(dec=75.0, rows=rows, cols=cols, overlap=overlap)
        assert allowed_error(group, plan.targets, rotating=False) == (
            pytest.approx(want, abs=1e-9))

    def test_the_two_cases_that_moved_were_loose_before(self):
        """The numbers this WP moved, from the tree before the change, so the
        fix is read as a fix and not as a re-pin: both were looser than the
        doctor's number."""
        assert 0.4940927501622061 > _doctor(dec=75.0, rows=1, cols=4,
                                            overlap=10)
        assert 6.363355801228608 > _doctor(dec=75.0, rows=1, cols=3,
                                           overlap=25)

    def test_the_rotating_path_is_the_groups_own_tolerance(self):
        """A rotator that corrects holds each panel to its own angle: the
        group's tolerance, skipped or not, as before."""
        for skip in ("", "1-2"):
            plan = _compiled(skip=skip)
            (group,) = plan.groups
            assert allowed_error(group, plan.targets, rotating=True) == (
                group.angle_tolerance_deg)
            assert group.angle_tolerance_deg == pytest.approx(2.117,
                                                              abs=0.005)

    def test_a_fixed_angle_block_carries_no_key_and_the_same_number(self):
        """"Camera fixed at PA" never corrected: its own tolerance already is
        the doctor's number, skipped or not, and nothing is recorded."""
        for skip in ("", "1-2"):
            plan = _compiled(skip=skip, angle="Camera fixed at PA")
            (group,) = plan.groups
            assert not group.rotate and KEY not in group.geometry
            doctor = _doctor(dec=75.0, rows=1, cols=4, overlap=10)
            for rotating in (True, False):
                assert allowed_error(group, plan.targets,
                                     rotating=rotating) == pytest.approx(
                    doctor, abs=1e-9)


def _engine(plan):
    eng = SequenceEngine.__new__(SequenceEngine)
    eng._plan = plan
    eng._groups = {g.id: g for g in plan.groups}
    eng._group_runs = {}
    eng._group_angle_read = {}
    eng.hub = SimpleNamespace(_rotation_trusted=False)
    return eng


async def test_a_camera_between_the_two_numbers_sets_the_group_aside():
    """The 1x4 with panel 1-2 skipped, rotation off tonight, the camera
    reading 0.48 deg off the layout angle: inside the 0.4941 the members gave
    and beyond the 0.4720 the doctor prints, so the fixed camera, which cannot
    turn itself, sets the group aside.

    Mutant "term rebuilt from the members": ``Failed: DID NOT RAISE``.
    """
    plan = _compiled(skip="1-2")
    group = plan.groups[0]
    rec = {"pa_deg": group.pa_deg + 0.48, "exposed_at": SINCE + 1.0}
    with pytest.raises(GroupSetAside):
        await _engine(plan)._group_angle_check(
            plan.targets[0], group, None, rec, SINCE)


class TestWhatTheCompileRecords:
    @pytest.mark.parametrize("rows,cols,overlap,skip,_before", GRIDS[:2])
    def test_a_corrected_group_records_the_number_the_doctor_prints(
            self, rows, cols, overlap, skip, _before):
        plan = _compiled(rows=rows, cols=cols, overlap=overlap, skip=skip)
        (group,) = plan.groups
        assert group.geometry[KEY] == pytest.approx(
            _doctor(dec=75.0, rows=rows, cols=cols, overlap=overlap),
            abs=1e-12)
        # The corrected group's own tolerance is a different number.
        assert group.angle_tolerance_deg > group.geometry[KEY]

    def test_the_other_keys_are_as_they_were(self):
        (group,) = _compiled(skip="1-2").groups
        assert set(group.geometry) == {"rows", "cols", "overlap", "fov_x",
                                       "fov_y", "fov_from", "key", KEY}
        assert group.geometry["key"] == _compiled(skip="").groups[0].geometry[
            "key"]

    @pytest.mark.parametrize("dec,overlap", [
        ("+80 00 00", 10),   # M15: convergence has spent the budget, 0.0
        ("+75 00 00", 0),    # no overlap: the share is infinite, the number 0
    ])
    def test_the_recorded_number_is_json_safe(self, dec, overlap):
        """A share can be infinite and is not JSON; the tolerance is finite in
        [0, 90] for every grid, and the plan dumps without ``Infinity``."""
        plan = _compiled(dec=dec, overlap=overlap, skip="1-2")
        (group,) = plan.groups
        value = group.geometry[KEY]
        assert math.isfinite(value) and 0.0 <= value <= 90.0
        assert value == pytest.approx(framing.angle_tolerance_deg(
            _spec(float(dec.split()[0]), overlap=overlap / 100.0)), abs=1e-12)
        json.dumps(plan.model_dump(mode="json"), allow_nan=False)

    def test_the_number_follows_the_layout_angle(self):
        """The share depends on the layout angle (A.1), and the relay copies
        ``geometry`` wholesale from a fresh compile (``_adopt_relaid``): a 3x3
        at Dec 75 records 4.60 at angle 0 and 3.61 at angle 90."""
        got = [_compiled(rows=3, cols=3, overlap=25, rotation=a
                         ).groups[0].geometry[KEY] for a in (0, 90)]
        assert got == [pytest.approx(4.60, abs=0.005),
                       pytest.approx(3.61, abs=0.005)]


class TestAGroupStoredBeforeTheNumberExisted:
    """No saved session changes behaviour: a group without the key reads from
    its members, exactly as it did (COMMON.md rule 14)."""

    @pytest.mark.parametrize("rows,cols,overlap,skip,before", GRIDS)
    def test_it_reads_as_it_did(self, rows, cols, overlap, skip, before):
        plan = _compiled(rows=rows, cols=cols, overlap=overlap, skip=skip)
        (group,) = plan.groups
        group.geometry.pop(KEY, None)
        assert allowed_error(group, plan.targets, rotating=False) == (
            pytest.approx(before, abs=1e-9))

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, True, "x",
                                     None])
    def test_an_unusable_number_falls_back_and_is_never_negative_or_nan(
            self, bad):
        plan = _compiled(skip="1-2")
        (group,) = plan.groups
        group.geometry[KEY] = bad
        got = allowed_error(group, plan.targets, rotating=False)
        assert got == pytest.approx(0.4940927501622061, abs=1e-9)
        assert math.isfinite(got) and got >= 0.0


#: sha256 of the plan dump (sorted keys, floats rounded to 9 decimals with no
#: negative zero as ``_rounded`` in test_w5_1b2_chain_phases.py does, because
#: panel geometry differs in its last bit between the Windows and the Linux C
#: library) of these compiles on origin/main c106b4b6, BEFORE this change.
#: The key is the only thing that moved: taken off every group's geometry the
#: dump hashes as it did (COMMON.md rule 14). Every other mosaic pin of the
#: suite (test_w6_1_rule14_pin.py, test_w5_1b2_chain_phases.py, the 2x2
#: golden) is a block that does not correct convergence and did not move.
BASE_PLAN_HASHES = {
    "1x4-skip-1-2":
        "7529436be2bca6e8d3be8770cf4ff196b74271461698e97087cc99244cb2e2f9",
    "1x4-whole":
        "76a43d319fb814bb07b1aafe325b68fbd23542a8a9024a398e4a116b89884f61",
    "3x3-skip-corner":
        "5b829e40481825ef81089ddab6e11f6eef9ba327d54397a743373970ca0ce218",
}
HASHED = {
    "1x4-skip-1-2": dict(skip="1-2"),
    "1x4-whole": dict(),
    "3x3-skip-corner": dict(rows=3, cols=3, overlap=25, skip="1-1"),
}


def test_a_corrected_plan_differs_from_before_by_the_key_alone():
    got = {}
    for name, kw in HASHED.items():
        dumped = _compiled(**kw).model_dump(mode="json")
        for g in dumped["groups"]:
            assert g["geometry"].pop(KEY) > 0.0
        got[name] = hashlib.sha256(json.dumps(
            _rounded(dumped), sort_keys=True).encode()).hexdigest()
    assert got == BASE_PLAN_HASHES
