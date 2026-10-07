# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Per-panel position angle: block PA plus each panel's meridian convergence
(#175, backlog WP-124, spec 2026-09-23 flows mosaic, I-26 and S8).

THE FAULT. ``compute_mosaic`` laid every panel on the grid's tangent plane
and the compile commanded the block's one angle to every panel. Local north
turns across a mosaic (5.97 deg at the corners of a 3x3 of 2.0 x 1.33 deg
panels at 25% overlap at Dec 75, 1.32 at Dec 41), so a rotator-commanded
block left wedge gaps at the corners at high declination or low overlap.

THE FIX AND ITS SIGN. A frame at sky position angle theta_i lies on the grid
when ``theta_i = theta + n_i``, ``n_i`` being the angle of local north at
that panel from +eta toward +xi (east): the camera's up for PA theta_i sits
at ``n_i - theta_i`` on the grid plane and the grid's up at ``-theta``. The
convention is the rig's, confirmed on a real solve (#175, 2026-10-07): image
up is north rotated toward WEST by CROTA2. ``test_a_frame_at_each_panels_pa_
lies_on_the_grid`` re-derives that from the projection itself rather than
from the formula under test, so a sign flip cannot agree with it.

THE LAYOUT ANGLE IS NOT MOVED. ``rotation_deg`` on a panel is the angle the
Atlas draws its rectangle at (``PanelLayer.tsx``) and ``_corners`` turns the
corners by; carrying the corrected angle there would draw the wedges the
correction removes. Two keys are added, ``convergence_deg`` and ``pa_deg``.

Each case names the mutant it was shown RED under, with the failure observed.
Every mutant was run from a byte backup inside the WP-124 worktree (#254),
restored byte-identically (sha256 compared) and grepped out.
"""
from __future__ import annotations

import math

import pytest

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store, panel)
from astrodeck.catalog import framing
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.config import RotatorConfig
from astrodeck.flows import check, to_plan
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import Target, TargetGroup
from astrodeck.sequence.session import session_store

#: A 3x3 of the spec's reference panels, laid out at angle 0 at Dec 75: the
#: grid issue #175 measured. The RA is a made-up one; nothing here moves
#: with it (Appendix A.1) and no site is involved.
SPEC_75 = dict(ra_hours=6.0, dec_deg=75.0, rows=3, cols=3, overlap=0.25,
               rotation_deg=0.0, fov_x_deg=2.0, fov_y_deg=1.33)
SPEC_41 = {**SPEC_75, "dec_deg": 41.0}


def _cells(spec) -> dict:
    return {(p["row"], p["col"]): p
            for p in framing.compute_mosaic(spec)["panels"]}


def _circ(a: float, b: float) -> float:
    """The distance between two angles, in [0, 180]."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ============================================================ compute_mosaic

class TestComputeMosaicCarriesTheCorrection:
    def test_the_corners_of_the_dec_75_3x3_turn_by_5_97_degrees(self):
        """Issue #175's golden vector: panel (0, 0), the north-west corner,
        is commanded 5.97 deg and (0, 2) 354.03 (north leans toward the
        pole, which on the grid plane is east of a panel west of the
        centre); the middle column holds the block angle; and EVERY panel's
        ``rotation_deg``, the layout angle the Atlas draws, is still 0.0.

        Mutant "n_i sign flipped" (``panel_convergence_deg`` returns
        ``-n``) failed:
            assert 354.03457031752134 == 5.97 ± 0.01
        Mutant "pa_deg = rotation_deg" (the panel loop writes the layout
        angle to ``pa_deg``) failed:
            assert 0.0 == 5.97 ± 0.01
        """
        cells = _cells(SPEC_75)
        assert cells[(0, 0)]["pa_deg"] == pytest.approx(5.97, abs=0.01)
        assert cells[(0, 2)]["pa_deg"] == pytest.approx(354.03, abs=0.01)
        assert cells[(0, 0)]["convergence_deg"] == pytest.approx(5.97,
                                                                 abs=0.01)
        assert cells[(0, 2)]["convergence_deg"] == pytest.approx(-5.97,
                                                                 abs=0.01)
        for r in range(3):
            mid = cells[(r, 1)]
            assert _circ(mid["pa_deg"], 0.0) < 1e-9, mid
            assert abs(mid["convergence_deg"]) < 1e-9, mid
        assert {p["rotation_deg"] for p in cells.values()} == {0.0}, (
            "rotation_deg is the layout angle the Atlas draws; the "
            "correction does not belong there")

    def test_dec_41_turns_the_corners_by_1_32_degrees(self):
        """The same grid at Dec 41, the issue's other figure.

        Mutant "n_i sign flipped" failed:
            assert 358.67626856853485 == 1.32 ± 0.01
        """
        cells = _cells(SPEC_41)
        assert cells[(0, 0)]["pa_deg"] == pytest.approx(1.32, abs=0.01)
        assert cells[(0, 2)]["pa_deg"] == pytest.approx(360.0 - 1.32,
                                                        abs=0.01)

    def test_pa_is_the_block_angle_plus_convergence_wrapped_into_0_360(self):
        """A block at layout 358 deg (the grid turned 2 deg the other way,
        so the corners are no longer symmetric): the north-west corner turns
        5.85 deg, 358 + 5.85 wraps to 3.85, and the north-east corner 351.92
        stays under 360; the centre panel keeps 358.0; ``rotation_deg`` still
        says 358; and no ``pa_deg`` reads 360 or more.

        Mutant "pa_deg not wrapped" (the ``% 360.0`` taken off the sum;
        the ``>= 360`` guard below it then reads 363.85 as 0) failed here:
            assert 0.0 == 3.85 ± 0.01
        and a sum below 0 stays negative, which the Dec 75 case above
        caught first:
            assert -5.965429682478653 == 354.03 ± 0.01
        """
        cells = _cells({**SPEC_75, "rotation_deg": 358.0})
        assert cells[(0, 0)]["pa_deg"] == pytest.approx(3.85, abs=0.01)
        assert cells[(0, 2)]["pa_deg"] == pytest.approx(351.92, abs=0.01)
        assert cells[(1, 1)]["pa_deg"] == pytest.approx(358.0, abs=1e-9)
        assert {p["rotation_deg"] for p in cells.values()} == {358.0}
        wrapped = 0
        for p in cells.values():
            assert 0.0 <= p["pa_deg"] < 360.0, p
            assert _circ(p["pa_deg"], 358.0 + p["convergence_deg"]) < 1e-9
            wrapped += 358.0 + p["convergence_deg"] >= 360.0
        assert wrapped >= 3, "the case must reach the wrap it is about"

    def test_a_tiny_negative_sum_wraps_to_zero_never_to_360(self):
        """``-1e-15 % 360.0`` is exactly ``360.0`` in floating point, which
        is not a position angle in [0, 360): a middle-column panel can come
        out a hair below the layout angle, and a block at 0 would then
        command 360. One panel at the tangent point turns by nothing, so a
        layout angle of -1e-15 IS that sum.

        Mutant "wrap hole open" (the ``pa_deg >= 360.0`` guard made
        unreachable) failed:
            AssertionError: {'col': 0, 'convergence_deg': 0.0, 'dec_deg':
            75.0, 'pa_deg': 360.0, ...}
            assert 360.0 == 0.0
        """
        spec = {**SPEC_75, "rows": 1, "cols": 1, "rotation_deg": -1e-15}
        [p] = framing.compute_mosaic(spec)["panels"]
        assert p["pa_deg"] == 0.0, p

    @pytest.mark.parametrize("layout", [0.0, 37.0, 90.0, 215.0])
    @pytest.mark.parametrize("dec", [75.0, 41.0])
    def test_a_frame_at_each_panels_pa_lies_on_the_grid(self, layout, dec):
        """THE SIGN, DERIVED FROM THE PROJECTION. Put the camera's up
        direction at a panel on the sky (north rotated toward WEST by the
        panel's ``pa_deg``, the rig's convention), step a small way along
        it, and project the step into the grid's tangent plane: it must
        point where the grid's own up points, ``-layout`` from +eta toward
        +xi. That is what "the frames lie on the grid" means, and it uses
        nothing of the formula the panel keys are made with.

        Mutant "n_i sign flipped" failed at layout 0, Dec 75:
            AssertionError: panel (0, 0): the camera up lands 11.933 deg
            from the grid's up
        and at the other seven layouts by 1.7 to 13.4 deg. Mutant "pa_deg
        = rotation_deg" failed the same way, at 5.965 deg for the same case.
        """
        spec = {**SPEC_75, "dec_deg": dec, "rotation_deg": layout}
        eps = 1e-4
        for p in framing.compute_mosaic(spec)["panels"]:
            pa = math.radians(p["pa_deg"])
            dec1 = p["dec_deg"] + eps * math.cos(pa)
            ra1 = p["ra_hours"] + (eps * -math.sin(pa)
                                   / math.cos(math.radians(p["dec_deg"]))
                                   / 15.0)
            x0, y0 = framing.project(p["ra_hours"], p["dec_deg"],
                                     spec["ra_hours"], spec["dec_deg"])
            x1, y1 = framing.project(ra1, dec1, spec["ra_hours"],
                                     spec["dec_deg"])
            up = math.degrees(math.atan2(x1 - x0, y1 - y0))
            off = _circ(up, -layout)
            # 0.05 deg, not rounding: the gnomonic is not conformal, so away
            # from the tangent point it stretches the radial direction by
            # (1 + rho^2) against the tangential one's sqrt(1 + rho^2), and
            # a step's direction on the grid plane is off the sky's by a
            # fraction of that. At the 3 deg of these corners it is 0.027 deg
            # at most (measured), 0.002 at layout 0; the sign flips these
            # cases guard are 12 deg.
            assert off < 0.05, (
                f"panel ({p['row']}, {p['col']}): the camera up lands "
                f"{off:.3f} deg from the grid's up")

    def test_a_single_panel_and_a_column_along_a_meridian_change_nothing(self):
        """At layout 0 a column of panels lies on the centre meridian and no
        panel is east or west of it, so local north is the grid's up at
        every one: ``pa_deg`` is the block's angle (to rounding) and nothing
        is commanded that was not. A single panel is the same case.

        The case guards the other direction from the golden vectors, a
        correction that invents a turn for a meridian column, e.g. mutant
        "convergence measured from a displaced tangent point"
        (``panel_convergence_deg(centre, spec.ra_hours + 0.1, ...)`` in the
        panel loop: the column no longer lies on the plane's centre
        meridian), which failed:
            AssertionError: {'col': 0, 'convergence_deg': 1.448910910921479,
            'dec_deg': 76.49591000829899, 'pa_deg': 1.448910910921479, ...}
            assert 1.448910910921479 < 1e-09
        """
        for rows, cols in ((4, 1), (1, 1)):
            spec = {**SPEC_75, "rows": rows, "cols": cols,
                    "rotation_deg": 0.0}
            for p in framing.compute_mosaic(spec)["panels"]:
                assert abs(p["convergence_deg"]) < 1e-9, p
                assert _circ(p["pa_deg"], 0.0) < 1e-9, p

    def test_every_panel_carries_exactly_the_old_keys_and_the_two_new(self):
        """The route answers ``compute_mosaic`` as it is, so this is the
        wire shape the UI mirror carries. ``rotation_deg`` stays.

        Mutant "convergence key renamed" (``"conv_deg": n_i`` in the panel
        dict) failed:
            AssertionError: assert {'col', 'conv...ion_deg', ...} == {'col',
            'conv...ion_deg', ...}
              Extra items in the left set:
              'conv_deg'
              Extra items in the right set:
              'convergence_deg'
        """
        for p in framing.compute_mosaic(SPEC_75)["panels"]:
            assert set(p) == {"row", "col", "ra_hours", "dec_deg",
                              "rotation_deg", "convergence_deg", "pa_deg"}

    def test_a_panel_at_the_pole_turns_by_the_ra_difference(self):
        """Within the probe's step of the pole the probe goes south and is
        reversed (``panel_convergence_deg``): at the pole local north is
        rotated from the grid's up by 15 deg per hour of RA from the
        tangent point, here one hour. A 3x3 laid out there, some of whose
        panels lie past the pole, answers a finite, wrapped angle at every
        panel, which the compile would command to a rotator. (The golden
        vector for the same case, to the bit, is in
        ``test_catalog_frame_id``.)

        Mutant "pole probe not reversed" (``sign`` fixed at 1) failed:
            assert -165.00054541329604 == 15.0 ± 0.001
        """
        n = framing.panel_convergence_deg(
            {"ra_hours": 3.0, "dec_deg": 89.99995}, 4.0, 89.5)
        assert n == pytest.approx(15.0, abs=0.001)
        spec = {**SPEC_75, "ra_hours": 3.0, "dec_deg": 89.9}
        for p in framing.compute_mosaic(spec)["panels"]:
            assert math.isfinite(p["convergence_deg"]), p
            assert 0.0 <= p["pa_deg"] < 360.0, p

    def test_the_public_name_is_the_one_function_the_old_name_calls(self):
        """``convergence_share`` and the spec-claims test read the private
        name; it stays, as the same function, so the share and the panel
        keys cannot be two measures of local north."""
        assert framing._local_north_deg is framing.panel_convergence_deg


# ================================================================== to_plan

CENTRE = {"name": "Probe", "ra": "06h 00m 00s", "dec": "+75 00 00"}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph(*, rows=3, cols=3, overlap=25, angle="Rotate to PA", rotation=0,
           **target) -> FlowGraph:
    """DUSK -> a framed TARGET -> a FILTER CYCLE with the loop wire back."""
    params = {**CENTRE, "fovX": 2.0, "fovY": 1.33, "rows": rows,
              "cols": cols, "overlap": overlap, "angle": angle,
              "rotation": rotation, **target}
    return FlowGraph(
        nodes=[_n("d", "dusk"), _n("t", "target", x=100, **params),
               _n("s0", "cycle", x=200)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s0", "run"),
               _e("s0", "pass", "t", "next")])


def _plan(graph: FlowGraph, *, rig=None):
    return to_sequence_plan(compile_plan(graph, "n"), graph,
                            flow_id="flow-w16", rig=rig)


def _spec(*, rows=3, cols=3, overlap=0.25, rotation=0.0, dec=75.0) -> dict:
    return {"ra_hours": parse_ra(CENTRE["ra"]), "dec_deg": dec, "rows": rows,
            "cols": cols, "overlap": overlap, "rotation_deg": rotation,
            "fov_x_deg": 2.0, "fov_y_deg": 1.33}


class TestTheCompileCommandsEachPanelItsOwnAngle:
    def test_a_rotating_block_commands_each_panels_pa(self):
        """The 3x3 at Dec 75, layout 0, 'Rotate to PA': each panel's
        ``rotation_deg`` is its own ``pa_deg`` from ``compute_mosaic`` (the
        north-west corner 5.97, the middle column 0, the north-east 354.03),
        INCLUDING at a block angle of 0 (a corrected corner is commanded
        although the block's angle is none to command). The group keeps the
        LAYOUT angle, ``pa_deg`` 0.0, and ``rotate``.

        Mutant "commanded stays the block's" (``rotation_deg:
        commanded``) failed:
            AssertionError: assert {(0, 0): 0.0,... 0): 0.0, ...} == {(0, 0):
            5.96...25976295, ...}
              Differing items:
              {(0, 0): 0.0} != {(0, 0): 5.965429682478653}
        """
        plan, _ = _plan(_graph())
        want = {(p["row"], p["col"]): p["pa_deg"]
                for p in framing.compute_mosaic(_spec())["panels"]}
        got = {(t.panel_row, t.panel_col): t.rotation_deg
               for t in plan.targets}
        assert got == want
        assert got[(0, 0)] == pytest.approx(5.97, abs=0.01)
        assert got[(0, 2)] == pytest.approx(354.03, abs=0.01)
        group = plan.groups[0]
        assert (group.pa_deg, group.rotate) == (0.0, True)

    def test_a_corrected_block_spends_no_overlap_on_convergence(self):
        """A.2 with c taken as 0: each panel is held to its own angle, so
        convergence no longer eats the budget, and the group's tolerance is
        the single-panel figure (6.36 deg for 2.0 x 1.33 deg at 25%), where
        an uncorrected block keeps 4.60 (A.2's table, Dec 75).

        Mutant "tolerance stays charged" (``framing.angle_tolerance_deg``
        of the whole grid for a corrected block) failed:
            assert 4.599688990615574 == 6.36 ± 0.005
        """
        plan, _ = _plan(_graph())
        assert plan.groups[0].angle_tolerance_deg == pytest.approx(
            6.36, abs=0.005)
        assert framing.angle_tolerance_deg(_spec()) == pytest.approx(
            4.60, abs=0.005)

    def test_a_fixed_camera_cannot_correct_and_keeps_its_old_plan(self):
        """'Camera fixed at PA' commands no panel an angle and stays
        charged for convergence (4.60 deg): a camera that cannot turn is
        laid at the block's angle and shot as it sits.

        Mutant "drop the rotate condition" (``corrects_convergence`` not
        asking whether the block rotates) failed:
            AssertionError: assert {0.0, 5.24186...97415025, ...} == {None}
              Extra items in the left set:
              0.0
              354.03457031752134
        """
        plan, _ = _plan(_graph(angle="Camera fixed at PA"))
        assert {t.rotation_deg for t in plan.targets} == {None}
        group = plan.groups[0]
        assert (group.pa_deg, group.rotate) == (0.0, False)
        assert group.angle_tolerance_deg == pytest.approx(4.60, abs=0.005)

    def test_the_constant_off_gives_the_old_plan_exactly(self, monkeypatch):
        """``CORRECT_MOSAIC_CONVERGENCE`` False is the plan every earlier
        build compiled: each panel at the block's angle, the tolerance
        charged. And it is the ONLY difference: dumped with the panels'
        ``rotation_deg`` and the group's tolerance set aside, the two
        plans are equal, so no id, schedule or step moved with the switch.

        Mutant "constant ignored" (``corrects_convergence`` not reading
        it) failed:
            assert {0.0, 5.24186...97415025, ...} == {0.0}
              Extra items in the left set:
              354.03457031752134
              5.965429682478653
        """
        monkeypatch.setattr(to_plan, "CORRECT_MOSAIC_CONVERGENCE", False)
        off, _ = _plan(_graph())
        assert {t.rotation_deg for t in off.targets} == {0.0}
        assert off.groups[0].angle_tolerance_deg == pytest.approx(
            framing.angle_tolerance_deg(_spec()), abs=1e-12)
        monkeypatch.setattr(to_plan, "CORRECT_MOSAIC_CONVERGENCE", True)
        on, _ = _plan(_graph())

        def bare(plan):
            d = plan.model_dump(mode="json")
            for t in d["targets"]:
                t.pop("rotation_deg")
            for g in d["groups"]:
                g.pop("angle_tolerance_deg")
            return d

        assert bare(on) == bare(off)

    def test_identity_does_not_move_with_the_correction(self, monkeypatch):
        """CONTINUE and a re-frame carry key on the group, the row and the
        column, and the block key on the LAYOUT angle, so every panel id
        and the group id are the same with the correction on and off.

        Mutant "block key moves with the correction" (the key's grid read
        through the switch, ``rows=nums["rows"] + (1 if corrected else 0)``)
        failed:
            AssertionError: assert ['6c4d205c50a...b1411d3', ...] ==
            ['37a196fa24c...a13641d', ...]
              At index 0 diff: '6c4d205c50a25781a41981f43441d2d4' !=
              '37a196fa24c259fdbc16e9e50f0bac8d'
        """
        on, _ = _plan(_graph())
        monkeypatch.setattr(to_plan, "CORRECT_MOSAIC_CONVERGENCE", False)
        off, _ = _plan(_graph())
        assert [t.id for t in on.targets] == [t.id for t in off.targets]
        assert on.groups[0].id == off.groups[0].id

    def test_a_block_whose_worst_turn_is_under_the_rotator_tolerance_is_left(
            self):
        """Dec 20: the worst panel is 0.55 deg off the grid, under the
        rotator's own 1 deg tolerance, so a rotation per hop would buy
        nothing and cost a one-sided approach each. Nothing is corrected:
        every panel at the block's angle, the tolerance charged (6.20, A.2).

        Mutant "no minimum turn" (``worst >= 0.0`` in
        ``corrects_convergence``) failed:
            assert {0.0, 0.54250...11714844, ...} == {0.0}
              Extra items in the left set:
              0.549420071143929
              359.4505799288561
        and the doctor case below, which asks the same function.
        """
        spec = _spec(dec=20.0)
        worst = max(abs(p["convergence_deg"])
                    for p in framing.compute_mosaic(spec)["panels"])
        assert worst < 1.0
        plan, _ = _plan(_graph(dec="+20 00 00"))
        assert {t.rotation_deg for t in plan.targets} == {0.0}
        assert plan.groups[0].angle_tolerance_deg == pytest.approx(
            framing.angle_tolerance_deg(spec), abs=1e-12)

    def test_the_minimum_turn_is_the_rotators_own_default(self):
        """One number, two files: ``RotatorConfig.tolerance_deg`` is what
        the rotate loop converges to, and the compile skips a turn under it.
        A change to the default turns this red rather than leaving the two
        to drift."""
        assert to_plan.CONVERGENCE_MIN_DEG == RotatorConfig().tolerance_deg

    @pytest.mark.parametrize("has_rotator,corrected",
                             [(None, True), (True, True), (False, False)])
    def test_a_rig_known_to_have_no_rotator_cannot_correct(
            self, has_rotator, corrected):
        """A camera turned by hand is a fixed camera: the correction needs a
        rotator, so a rig that says it has none keeps the block's angle on
        every panel and the charged tolerance. Unknown (None) is not no.

        Mutant "rig ignored" (the ``has_rotator is False`` line made
        unreachable) failed the False case:
            AssertionError: {0.0, 5.241865267159233, 5.580364025976295,
            5.965429682478653, 354.03457031752134, 354.41963597415025, ...}
            assert (7 > 1) is False
        and the doctor's no-rotator case below.
        """
        plan, _ = _plan(_graph(), rig=RigFacts(has_rotator=has_rotator))
        angles = {t.rotation_deg for t in plan.targets}
        assert (len(angles) > 1) is corrected, angles


# ================================================================== doctor

M6 = "convergence turns neighbouring panels"
M15 = "leaves no room for camera angle error"


def _doctor_graph(*, angle, dec="+75 00 00", rotation=0, overlap=10,
                  rows=1, cols=4) -> FlowGraph:
    block = {**CENTRE, "dec": dec, "rows": rows, "cols": cols,
             "overlap": overlap, "fovX": 2.0, "fovY": 1.33, "angle": angle,
             "rotation": rotation, "counts": "Accepted subs"}
    return FlowGraph(
        nodes=[_n("d", "dusk", 0, 0, stop="Dawn"),
               _n("t", "target", 200, 0, **block),
               _n("af", "autofocus", 400), _n("g", "guide", 600),
               _n("cy", "cycle", 800, plan="L 60, R 60, G 60, B 60"),
               _n("r", "report", 1000)],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "af", "run"),
               _e("af", "focused", "g", "run"), _e("g", "guiding", "cy", "run"),
               _e("cy", "complete", "r", "session"),
               _e("cy", "pass", "t", "next")])


def _said(issues, marker):
    return [i for i in issues if marker in i.text]


class TestTheDoctorStopsChargingACorrectedBlock:
    def test_a_rotating_4x1_at_dec_75_is_not_warned_of_convergence(self):
        """The 4x1 at 10% at Dec 75 uses 38.9% of its overlap if the camera
        is fixed (M6) and 58.8% at Dec 80 (M15): a ROTATING block corrects
        each panel, so neither says anything. Rule M6/M15's premise, one
        angle for every panel, no longer holds for it.

        Mutant "doctor charges every block" (``if False and
        to_plan.corrects_convergence(``, so M6 and M15 read every block's
        share, as before) failed:
            AssertionError: ('+75 00 00', [Issue(text='▸ TARGET Probe - at
            Dec 75 meridian convergence turns neighbouring panels against
            each other, which uses 38.9% of their 10% overlap. Widen the
            overlap or use fewer columns.', level='warn')])
        """
        for dec in ("+75 00 00", "+80 00 00"):
            issues = check(_doctor_graph(angle="Rotate to PA", dec=dec))
            assert not _said(issues, M6), (dec, issues)
            assert not _said(issues, M15), (dec, issues)

    def test_a_fixed_4x1_is_still_warned(self):
        """The same block with the camera fixed keeps M6 at Dec 75 (38.9%)
        and M15 at Dec 80: a fixed camera cannot correct, so the share it
        is charged is real.

        Mutant "drop the rotate condition" (the doctor asks
        ``corrects_convergence`` with ``rotate=True`` for every block, so a
        fixed one reads as corrected too) failed:
            AssertionError: []
        and so did the same mutant in ``to_plan.corrects_convergence``.
        """
        share = framing.convergence_share(
            {**_spec(rows=1, cols=4, overlap=0.10)})
        assert share == pytest.approx(0.389, abs=0.0005)
        warn = _said(check(_doctor_graph(angle="Camera fixed at PA")), M6)
        assert len(warn) == 1 and "38.9%" in warn[0].text, warn
        danger = _said(check(_doctor_graph(angle="Camera fixed at PA",
                                           dec="+80 00 00")), M15)
        assert len(danger) == 1 and danger[0].level == "danger", danger

    def test_with_the_constant_off_the_rotating_block_is_warned_again(
            self, monkeypatch):
        """The doctor and the compile read one switch: with the correction
        off the rotating block is charged for convergence, because that is
        the plan it compiles to.

        Mutant "constant ignored" (``corrects_convergence`` not reading the
        switch) failed:
            AssertionError: []
            assert 0 == 1
        """
        monkeypatch.setattr(to_plan, "CORRECT_MOSAIC_CONVERGENCE", False)
        issues = check(_doctor_graph(angle="Rotate to PA"))
        assert len(_said(issues, M6)) == 1, issues

    def test_a_rig_with_no_rotator_is_still_charged(self):
        """No rotator, no correction (the compile keeps the block's angle
        for it), so the rotating 4x1 at Dec 75 is warned as a fixed one
        is, beside M8's turn-it-by-hand.

        Mutant "rig ignored" (``corrects_convergence`` not asking the rig)
        failed:
            AssertionError: [Issue(text='▸ TARGET Probe - no rotator: turn
            the camera by hand to PA 0.0 before the first panel. ...
            assert 0 == 1
        """
        issues = check(_doctor_graph(angle="Rotate to PA"),
                       rig=RigFacts(has_rotator=False))
        assert len(_said(issues, M6)) == 1, issues

    def test_a_block_the_compile_leaves_uncorrected_is_still_charged(self):
        """The doctor asks the compile's own question. A 3x1 at Dec 10 and
        0.5% overlap turns 0.35 deg at its ends, under the rotator's 1 deg,
        so the compile keeps the block's angle and the convergence is
        charged: 40% of a 0.5% overlap, which M6 says.

        Mutant "no minimum turn" (``worst >= 0.0`` in
        ``corrects_convergence``, which the doctor asks) failed:
            AssertionError: []
            assert 0 == 1
        """
        spec = _spec(rows=1, cols=3, overlap=0.005, dec=10.0)
        worst = max(abs(p["convergence_deg"])
                    for p in framing.compute_mosaic(spec)["panels"])
        assert worst < 1.0
        assert 0.25 < framing.convergence_share(spec) < 0.5
        issues = check(_doctor_graph(angle="Rotate to PA", dec="+10 00 00",
                                     rows=1, cols=3, overlap=0.5))
        assert len(_said(issues, M6)) == 1, issues
        plan, _ = _plan(_graph(rows=1, cols=3, overlap=0.5, dec="+10 00 00"))
        assert {t.rotation_deg for t in plan.targets} == {0.0}


# ================================================================== engine

PA, TOL = 30.0, 4.0
ROTATING = {"pa_deg": PA, "angle_tolerance_deg": TOL, "rotate": True}
CORNER = 36.0       # panel 1-1's own commanded angle: 6 deg off the group's


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _corrected_plan():
    """A 2x2 rotating group at PA 30 with panel 1-1 commanded its own 36,
    as a corrected block's compile gives it."""
    plan = grid_plan(group_kw=ROTATING)
    plan.targets[0].rotation_deg = CORNER
    return plan


async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


class TestTheAngleCheckReadsEachPanelsOwnAngle:
    async def test_a_corner_measuring_its_own_pa_reads_ok(self, group_hub,
                                                          monkeypatch):
        """Panel 1-1 is commanded 36.0 against a group angle of 30.0 and a
        tolerance of 4.0, and its centring measures 36.0: on target. Judged
        against the group's 30.0 that is 6.0 off and the panel would be
        deferred, pass after pass, by the very correction meant to help it.
        Every panel is shot on its first visit and nothing is set aside.

        Mutant "use group.pa_deg" (``_planned_pa`` answering the group's
        angle for every panel, so both the angle check and
        ``_hop_angle_within`` read it) failed:
            AssertionError: ('1-1', [(1788314109.0, 'info', 'target 4/4: M31
            2-2'), ...])
            assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
              Right contains 6 more items, first extra item: 'L'
        """
        night = await _night(
            group_hub, monkeypatch, _corrected_plan(),
            sky=lambda who, n: CORNER if who == _name("1-1") else PA)
        assert night.done, night.trace[-3:]
        for label in ("1-1", "1-2", "2-2", "2-1"):
            assert _shot(night, label) == ["L", "R"] * 3, (
                label, night.lines[-4:])
        assert not night.said("did not bring the camera"), night.lines
        assert night.stored.set_aside == []

    async def test_a_corner_left_at_the_block_angle_is_off_its_own(
            self, group_hub, monkeypatch):
        """The other direction: the rotator did not turn the corner, which
        still reads the group's 30.0, 6.0 off the 36.0 it was commanded and
        beyond the 4.0 tolerance. It is deferred (a rotator can put that
        right) and the deferral names the panel's own angle.

        Mutant "use group.pa_deg" failed:
            AssertionError: [(1788313689.0, 'info', "sequence 'M31 mosaic'
            started: 24 frames, 12 min integration"), ...]
            assert []
        (nothing said of 1-1: the corner was judged against 30.0 and shot)
        """
        night = await _night(group_hub, monkeypatch, _corrected_plan(),
                             sky=lambda who, n: PA)
        said = night.said("on 1-1")
        assert said, night.lines
        assert "laid out at 36.0" in said[0], said[0]
        assert _shot(night, "1-2") == ["L", "R"] * 3

    async def test_a_stopped_short_corner_inside_its_own_tolerance_is_waived(
            self, group_hub, monkeypatch):
        """The hop's other reading of the angle, `_hop_angle_within`, in
        the real hop: the hub reports panel 1-1's rotation stopped short
        (``rotation_skipped``, #526) and its centring measured 36.0, the
        corner's own angle and 6.0 off the group's 30.0 against a 4.0
        tolerance. The camera is within tolerance of where this panel is
        planned, so the deferral is waived and the panel shoots on its first
        visit; judged against the group's angle it would be deferred for a
        rotation that put it exactly where it was told to be.

        Mutant "target ignored" (``_hop_angle_within`` reading the group's
        angle) failed:
            AssertionError: [(1788314109.0, 'info', 'target 4/4: M31 2-2'),
            ...]
            assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
              Right contains 6 more items, first extra item: 'L'
        """
        def goto(who, n, result):
            if who == _name("1-1"):
                return {**result, "rotation_skipped": True}
            return result

        night = await _night(
            group_hub, monkeypatch, _corrected_plan(), goto=goto,
            sky=lambda who, n: CORNER if who == _name("1-1") else PA)
        assert night.done, night.trace[-3:]
        assert _shot(night, "1-1") == ["L", "R"] * 3, night.lines[-4:]
        assert not night.said("did not turn the camera"), night.lines

    async def test_with_rotation_off_a_fixed_camera_is_judged_at_the_block(
            self, group_hub, monkeypatch):
        """D-05: a rotator the self-test found not following is no rotator
        tonight, so the camera sits at one angle, the block's, for every
        panel. A corner commanded 36.0 cannot be turned to it; judged
        against 36.0 it would set the whole group aside for an angle nothing
        can reach. It is judged against the group's 30.0, where the camera
        is, and every panel shoots.

        Mutant "per-panel angle even with rotation off" (``_planned_pa``
        used for a camera that cannot turn) failed, the group set aside on
        its first hop ("the mosaic is set aside for tonight"):
            AssertionError: ('1-1', [...])
            assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
              Right contains 6 more items, first extra item: 'L'
        """
        group_hub._rotation_trusted = False
        night = await _night(group_hub, monkeypatch, _corrected_plan(),
                             sky=lambda who, n: PA)
        assert night.done, night.trace[-3:]
        for label in ("1-1", "1-2", "2-2", "2-1"):
            assert _shot(night, label) == ["L", "R"] * 3, (
                label, night.lines[-4:])
        assert night.stored.set_aside == []

    async def test_the_rotators_report_is_evidence_of_the_panels_own_angle(
            self, group_hub, monkeypatch):
        """THE ROTATOR'S OWN READING STANDS IN FOR A MISSING SOLVE (spec 5.6
        step 4), AND IT IS READ AGAINST THE PANEL'S OWN ANGLE. No centring
        measures an angle, the sim rotator is calibrated and reads 36.0, the
        angle panel 1-1 was commanded: the report is evidence the camera is
        where this panel was told to put it, so 1-1 shoots on its first
        visit, with the warning that says whose word it is. The other three
        panels are planned at the group's 30.0, which a rotator reading 36.0
        is 6.0 away from, past its 1.0 tolerance: no evidence for them, and
        they are deferred, so the rotator's word is not simply believed.

        Mutant "evidence read against the group's angle"
        (``_rotator_evidence(group.pa_deg, centring)`` in
        ``_group_angle_check``, where ``_planned_pa`` picks the panel's own)
        failed:
            AssertionError: [(1788314289.0, 'info', 'target 4/4: M31 2-2'),
            (1788314289.0, 'warning', "M31: angle not measured on 2-2 on 3
            consecu... set aside for tonight: a restart tonight does not
            retry it, the next night does")]
            assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
              Right contains 6 more items, first extra item: 'L'
        (the corner was judged against 30.0, the rotator's 36.0 was no
        evidence, and 1-1 was deferred with the rest)
        """
        rot = group_hub.devices["rotator"]
        await rot.sync(CORNER)
        night = await _night(group_hub, monkeypatch, _corrected_plan(),
                             sky=lambda who, n: None)
        assert night.done, night.trace[-3:]
        assert _shot(night, "1-1") == ["L", "R"] * 3, night.lines[-4:]
        said = night.said("the calibrated rotator's own report")
        assert said and "(it reads PA 36.0)" in said[0], said
        for label in ("1-2", "2-2", "2-1"):
            assert _shot(night, label) == [], label


def _group(**kw) -> TargetGroup:
    return TargetGroup(id="g", name="M31", **{**ROTATING, **kw})


def _rec(pa: float, exposed_at: float) -> dict:
    return {"pa_deg": pa, "exposed_at": exposed_at}


class TestThePlannedAngle:
    def test_a_rotating_member_plans_its_own_angle(self):
        """``_planned_pa``: a rotate group's member with an angle of its own
        plans that one; without one, or in a fixed group, the group's.

        Mutant "use group.pa_deg" failed:
            assert 30.0 == 36.0
        """
        corner = Target(id="a", name="a", ra_hours=1.0, dec_deg=40.0,
                        rotation_deg=CORNER)
        bare = Target(id="b", name="b", ra_hours=1.0, dec_deg=40.0)
        assert SequenceEngine._planned_pa(corner, _group()) == CORNER
        assert SequenceEngine._planned_pa(bare, _group()) == PA
        assert SequenceEngine._planned_pa(
            corner, _group(rotate=False)) == PA

    def test_a_stopped_short_rotation_is_judged_at_the_panels_angle(self):
        """``_hop_angle_within``: the camera measured 36.0 on a hop whose
        rotation stopped short, at the corner commanded 36.0 with a 4.0
        tolerance: within it, so the deferral is waived. Without the target
        the group's 30.0 is 6.0 away and it stands.

        Mutant "target ignored" (``_hop_angle_within`` reading the group's
        angle) failed:
            AssertionError: assert False
             +  where False = <function SequenceEngine._hop_angle_within at
             0x...>(TargetGroup(id='g', name='M31', ...
        """
        corner = Target(id="a", name="a", ra_hours=1.0, dec_deg=40.0,
                        rotation_deg=CORNER)
        rec = _rec(CORNER, 100.0)
        assert SequenceEngine._hop_angle_within(
            _group(), rec, 99.0, target=corner)
        assert not SequenceEngine._hop_angle_within(_group(), rec, 99.0)
