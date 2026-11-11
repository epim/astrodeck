# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One tolerance for a corrected group, on both paths (#787, #828; #1116 C-10).

A block that corrects meridian convergence commands each panel its own angle
and carries A.2's tolerance with convergence taken as 0
(``to_plan.corrected_tolerance_deg``). When the nightly rotator self-test
fails (D-05) the same group is shot by a camera that sits at the block's
angle and cannot correct, so the convergence it can no longer remove is spent
out of the overlap again, as it is for any fixed camera
(``framing.angle_tolerance_deg``). ``angle_check.allowed_error`` is the one
function that answers both: the group's tolerance plus the convergence term,
which is 0 while the rotator corrects and the share fixed-camera convergence
uses when it does not.

The fixture is a 4x1 of 2.0 x 1.33 deg panels at 10 percent overlap on a
made-up centre at Dec 75, compiled by the real ``to_plan``: convergence uses
38.9 percent of its overlap, the corrected tolerance is 2.12 deg and a fixed
camera's is 0.47 deg.

Named mutant: the convergence term forced to 0 in ``allowed_error``, so the
fallback answers the group's corrected tolerance. Four cases in the first
class fail with it: 2.117 where 0.472 is due, a camera 1.0 deg off reading
ok, a Dec 80 grid allowing 2.117 where it allows 0, and 6.36 where 4.60 is
due.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog import framing
from astrodeck.catalog.coords import parse_ra
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.angle_check import allowed_error, angle_verdict
from astrodeck.sequence.models import Target, TargetGroup

CENTRE = {"name": "Probe", "ra": "06h 00m 00s"}
SINCE = 100.0


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _compiled(*, dec: str, angle: str = "Rotate to PA", rows: int = 1,
              cols: int = 4, overlap: int = 10, rotation: float = 0):
    """The group and members the real compile makes for a framed block."""
    params = {**CENTRE, "dec": dec, "fovX": 2.0, "fovY": 1.33, "rows": rows,
              "cols": cols, "overlap": overlap, "angle": angle,
              "rotation": rotation}
    graph = FlowGraph(
        nodes=[_n("d", "dusk"), _n("t", "target", x=100, **params),
               _n("s0", "cycle", x=200)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s0", "run"),
               _e("s0", "pass", "t", "next")])
    plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                               flow_id="flow-w2-7", rig=None)
    return plan.groups[0], plan.targets


def _spec(dec: float, *, rows=1, cols=4, overlap=0.10) -> dict:
    return {"ra_hours": parse_ra(CENTRE["ra"]), "dec_deg": dec, "rows": rows,
            "cols": cols, "overlap": overlap, "rotation_deg": 0.0,
            "fov_x_deg": 2.0, "fov_y_deg": 1.33}


def _judged(group: TargetGroup, members, *, rotating: bool,
            off_by: float) -> str:
    """The verdict kind for a camera reading ``off_by`` deg away from the
    layout angle, under the tolerance ``allowed_error`` gives the path."""
    rec = {"pa_deg": group.pa_deg + off_by, "exposed_at": SINCE + 1.0}
    return angle_verdict(
        rec, planned_pa_deg=group.pa_deg, since_ts=SINCE,
        tolerance_deg=allowed_error(group, members, rotating=rotating)).kind


class TestACorrectedGroupAtHighDeclination:
    def test_the_rotating_path_keeps_the_groups_tolerance(self):
        """While the rotator corrects, the convergence term is 0 and the
        answer is the group's own number: A.2 with no convergence charged.
        """
        group, members = _compiled(dec="+75 00 00")
        assert group.rotate and group.angle_tolerance_deg == pytest.approx(
            2.117, abs=0.005)
        assert allowed_error(group, members, rotating=True) == (
            group.angle_tolerance_deg)

    def test_the_fallback_is_the_fixed_cameras_tolerance(self):
        """With rotation off tonight the camera is a fixed one: the tolerance
        is what the doctor and the modal print for the grid
        (``framing.angle_tolerance_deg``), 0.47 deg, and not the corrected
        2.12.

        Mutant "convergence 0 in the fallback" (the term forced to 0): the
        fallback answers 2.117 and this assertion fails.
        """
        group, members = _compiled(dec="+75 00 00")
        fixed = framing.angle_tolerance_deg(_spec(75.0))
        assert fixed == pytest.approx(0.472, abs=0.005)
        assert allowed_error(group, members, rotating=False) == pytest.approx(
            fixed, abs=1e-9)

    def test_a_camera_1_deg_off_passes_rotating_and_fails_the_fallback(self):
        """The #828 hole, through the verdict: a fixed camera 1.0 deg off the
        layout angle is inside the corrected 2.12 and beyond the 0.47 a fixed
        camera is held to. Judged by the corrected tolerance it passed with
        holes between the panels.

        Mutant "convergence 0 in the fallback": the fallback verdict is ok.
        """
        group, members = _compiled(dec="+75 00 00")
        assert _judged(group, members, rotating=True, off_by=1.0) == "ok"
        assert _judged(group, members, rotating=False, off_by=1.0) == "off"
        assert _judged(group, members, rotating=False, off_by=0.3) == "ok"

    def test_where_convergence_spends_the_whole_budget_nothing_passes(self):
        """At Dec 80 a 4x1 at 10 percent has convergence using 59 percent of
        the overlap (M15): a fixed camera has no angle to spend, so the
        tolerance is 0 and a reading 0.1 deg off is off. Never negative.
        """
        group, members = _compiled(dec="+80 00 00")
        assert allowed_error(group, members, rotating=False) == 0.0
        assert allowed_error(group, members, rotating=True) > 2.0
        assert _judged(group, members, rotating=False, off_by=0.1) == "off"
        assert _judged(group, members, rotating=False, off_by=0.0) == "ok"

    def test_the_fallback_matches_the_doctor_for_a_grid(self):
        """A 3x3 at 25 percent at Dec 75 (the #787 numbers: 6.36 corrected,
        4.60 fixed) agrees with ``framing.angle_tolerance_deg`` on both."""
        group, members = _compiled(dec="+75 00 00", rows=3, cols=3,
                                   overlap=25)
        assert allowed_error(group, members, rotating=True) == pytest.approx(
            6.36, abs=0.005)
        assert allowed_error(group, members, rotating=False) == pytest.approx(
            framing.angle_tolerance_deg(_spec(75.0, rows=3, cols=3,
                                              overlap=0.25)), abs=1e-9)
        assert allowed_error(group, members, rotating=False) == pytest.approx(
            4.60, abs=0.005)


class TestAGroupThatNeverCorrected:
    def test_a_fixed_block_is_not_charged_twice(self):
        """'Camera fixed at PA' already carries the convergence in its
        tolerance and gives every panel the block's angle, so neither path
        takes anything more from it."""
        group, members = _compiled(dec="+75 00 00", angle="Camera fixed at PA")
        assert not group.rotate
        for rotating in (True, False):
            assert allowed_error(group, members, rotating=rotating) == (
                group.angle_tolerance_deg)
        assert group.angle_tolerance_deg == pytest.approx(0.472, abs=0.005)

    def test_a_group_with_no_geometry_or_no_tolerance_is_left_alone(self):
        """A hand-built group (the Plan UI's, or a test's) with no grid
        geometry has nothing to charge convergence against: its tolerance
        stands, and a group with none has no check."""
        bare = TargetGroup(id="g", name="M31", pa_deg=30.0, rotate=True,
                           angle_tolerance_deg=4.0)
        member = Target(id="a", name="a", ra_hours=1.0, dec_deg=40.0,
                        rotation_deg=36.0, panel_row=0, panel_col=0)
        assert allowed_error(bare, [member], rotating=False) == 4.0
        none = TargetGroup(id="g", name="M31", pa_deg=30.0, rotate=True)
        assert allowed_error(none, [member], rotating=False) is None


class TestTheConvergenceTermIsRead:
    def test_a_missing_panel_is_a_missing_seam_not_a_crash(self):
        """A skipped panel is not a member. For a group stored before the
        compile recorded the doctor's number (``fixed_tolerance_deg``, #1175)
        the seams that remain still charge their convergence, and the answer
        stays finite and between 0 and the group's tolerance."""
        group, members = _compiled(dec="+75 00 00")
        group.geometry.pop("fixed_tolerance_deg")
        kept = [m for m in members if m.panel_col != 1]
        got = allowed_error(group, kept, rotating=False)
        assert math.isfinite(got)
        assert 0.0 <= got <= group.angle_tolerance_deg

    def test_members_with_no_row_or_column_charge_nothing(self):
        """For a group stored before ``fixed_tolerance_deg`` (#1175): with
        the number recorded the members are not read at all."""
        group, members = _compiled(dec="+75 00 00")
        group.geometry.pop("fixed_tolerance_deg")
        loose = [m.model_copy(update={"panel_row": None, "panel_col": None})
                 for m in members]
        assert allowed_error(group, loose, rotating=False) == (
            group.angle_tolerance_deg)
