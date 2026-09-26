"""The overlap budget for angle: meridian convergence and a fixed camera's
angle error (#189; spec 2.4 ANGLE, A.1, A.2 and its Revision 1 combined rule).

Every panel of a grid is shot at one camera angle, measured against LOCAL
north, and local north is not one direction across a wide grid: laid out on
the grid's tangent plane, the panels east and west of the centre are turned
against each other by meridian convergence. Their shared edge then swings,
and at the far corner it eats into the overlap. ``framing.convergence_share``
is that share (A.1). A fixed camera that is off its planned angle turns every
panel about its own centre and eats the same overlap from the same corners,
so ``framing.angle_tolerance_deg`` budgets the two together: convergence and
angle error may use at most half the overlap between them,
``k = max(0, 0.5 - c)``, and the other half is left for pointing error. The
angle that spends ``k`` is A.2's formula, whose one copy is
``sequence.angle_check.angle_tolerance_deg`` (pinned by
``test_angle_check.py``); ``framing`` supplies the grid's ``k``.

Every figure here was recomputed with the repo's ``compute_mosaic`` and
``project`` before it was pinned, and each agrees with Appendix A.1 and A.2.

ONE GENERALISATION OF A.1, pinned below. A.1 was computed at angle 0, where
the panels that differ in RA are the ones side by side in a row, so its
formula weighs only those pairs: the swing of a shared VERTICAL edge,
``(f_y / 2) sin(delta)``, against the horizontal overlap ``overlap x f_x``.
At any other angle the pairs in a column differ in RA too, and at 90 deg they
are the only ones that do. ``convergence_share`` weighs both kinds, each
against its own overlap, and agrees with A.1 wherever A.1 was measured
(#321 asks for the spec's A.1 to say so).

Every test names the mutation it guards, of ``catalog/framing.py`` or, where
the formula is the one it delegates to, of ``sequence/angle_check.py``, and
quotes the failure that mutation produced, each run in a private copy of
server/ (scratchpad s3-G-mut), never in the shared tree.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog import framing


def spec(rows, cols, overlap, dec, *, rotation=0.0, fov=(2.0, 1.33)):
    """A grid of ``rows`` x ``cols`` 2.0 x 1.33 deg panels (unless told
    otherwise) at an arbitrary RA; nothing here is site data."""
    return {"ra_hours": 3.0, "dec_deg": dec, "rows": rows, "cols": cols,
            "overlap": overlap, "rotation_deg": rotation,
            "fov_x_deg": fov[0], "fov_y_deg": fov[1]}


class TestConvergenceShare:
    def test_the_two_pinned_grids(self):
        """38.9% for a 1x4 at 10% at Dec 75, which trips M6; 3.1% for a 3x3
        at 25% at Dec 41, which does not.

        Mutant "the grid's centre is north" (local north taken as the
        tangent plane's +eta axis at every panel, so no panel turns) failed:
            assert 0.0 == 0.38851 ± 5.0e-05
        """
        assert framing.convergence_share(spec(1, 4, 0.10, 75.0)) == \
            pytest.approx(0.38851, abs=5e-5)
        assert framing.convergence_share(spec(3, 3, 0.25, 41.0)) == \
            pytest.approx(0.03072, abs=5e-5)

    @pytest.mark.parametrize("rows, cols, overlap, dec, printed", [
        (3, 3, 0.25, 20.0, 1.3),
        (3, 3, 0.25, 41.0, 3.1),
        (3, 3, 0.25, 60.0, 6.2),
        (3, 3, 0.25, 75.0, 13.8),
        (1, 4, 0.10, 41.0, 9.1),
        (1, 4, 0.10, 75.0, 38.9),
    ])
    def test_appendix_a1_is_what_the_code_computes(self, rows, cols, overlap,
                                                   dec, printed):
        """Every share A.1 prints, to the tenth of a percent it prints.

        Mutant "overlap measured across the rows" (the horizontal pairs'
        swing divided by ``overlap x f_y``) failed every row, for example:
            assert 4.6 == 3.1
             +  where 4.6 = round((0.04620283324663597 * 100), 1)
            assert 58.4 == 38.9
             +  where 58.4 = round((0.5842214076188322 * 100), 1)
        """
        got = framing.convergence_share(spec(rows, cols, overlap, dec))
        assert round(got * 100, 1) == printed

    def test_a_grid_turned_90_degrees_is_weighed_down_its_columns(self):
        """At angle 90 a 3x3's rows run north-south, so its COLUMNS are what
        differ in RA. Weighed on the rows alone, as A.1's angle-0 formula
        reads, the 3x3 at Dec 75 would claim 0.9% where its column seams use
        21.6%, and the angle budget would hand that difference to a fixed
        camera as tolerance it does not have.

        Mutant "horizontal pairs only" (A.1's formula, rows alone) failed:
            assert 0.009313633952095435 == 0.21601 ± 5.0e-05
        """
        assert framing.convergence_share(
            spec(3, 3, 0.25, 75.0, rotation=90.0)) == pytest.approx(
                0.21601, abs=5e-5)
        # A single row along a meridian has one RA, so nothing converges.
        assert framing.convergence_share(
            spec(1, 4, 0.10, 75.0, rotation=90.0)) == pytest.approx(
                0.0, abs=1e-9)

    def test_controls(self):
        """A single panel has no neighbour to turn against. With no overlap
        at all, any turn opens a gap, and the share says so without dividing
        by zero.

        Mutant "a zero strip unguarded" (``_seam_share`` divides whatever the
        width) failed:
            ZeroDivisionError: float division by zero
        """
        assert framing.convergence_share(spec(1, 1, 0.25, 75.0)) == 0.0
        assert framing.convergence_share(spec(1, 4, 0.0, 41.0)) == math.inf


class TestAngleTolerance:
    def test_the_two_pinned_grids(self):
        """5.97 deg for the 3x3 at 25% at Dec 41 (c = 3.1%), and 0.47 deg for
        the 1x4 at 10% at Dec 75 (c = 38.9%).

        Mutant "k = 0.5 ignoring convergence" failed:
            assert 6.363355801228608 == 5.971 ± 5.0e-04
        """
        assert framing.angle_tolerance_deg(spec(3, 3, 0.25, 41.0)) == \
            pytest.approx(5.971, abs=5e-4)
        assert framing.angle_tolerance_deg(spec(1, 4, 0.10, 75.0)) == \
            pytest.approx(0.472, abs=5e-4)

    @pytest.mark.parametrize("fov, overlap, printed", [
        ((2.0, 1.33), 0.25, 6.36),
        ((2.0, 1.33), 0.15, 3.36),
        ((2.0, 1.33), 0.10, 2.12),
        ((0.9, 0.6), 0.25, 6.38),
    ])
    def test_the_k_half_table_is_a_single_panel(self, fov, overlap, printed):
        """A.2's first table is the rule with no convergence at all, which is
        exactly a 1x1: it has no neighbour, so c = 0 and k = 0.5. The formula
        is ``angle_check.angle_tolerance_deg``'s, which this delegates to, so
        the mutant below was made there, in the private copy.

        Mutant "sin(theta) = k x the wider ratio" (``max`` for ``min`` in
        ``angle_check``) failed every row:
            assert 14.51 == 6.36
             +  where 14.51 = round(14.514592245567865, 2)
            assert 14.48 == 6.38
             +  where 14.48 = round(14.477512185929927, 2)
        """
        got = framing.angle_tolerance_deg(spec(1, 1, overlap, 41.0, fov=fov))
        assert round(got, 2) == printed

    @pytest.mark.parametrize("rows, cols, overlap, dec, printed", [
        (3, 3, 0.25, 20.0, 6.20),
        (3, 3, 0.25, 41.0, 5.97),
        (3, 3, 0.25, 60.0, 5.57),
        (3, 3, 0.25, 75.0, 4.60),
        (1, 4, 0.10, 41.0, 1.73),
        (1, 4, 0.10, 75.0, 0.47),
    ])
    def test_appendix_a2_combined_is_what_the_code_computes(
            self, rows, cols, overlap, dec, printed):
        """A.2's combined table, every row, to the hundredth it prints.

        Mutant "k = 0.5 ignoring convergence" failed all six rows, for
        example:
            assert 6.36 == 5.97
             +  where 6.36 = round(6.363355801228608, 2)
            assert 2.12 == 0.47
             +  where 2.12 = round(2.1172425640746666, 2)
        """
        got = framing.angle_tolerance_deg(spec(rows, cols, overlap, dec))
        assert round(got, 2) == printed

    def test_a_grid_convergence_has_spent_has_no_angle_left(self):
        """M15: a 1x4 at 5% at Dec 75 spends 77.7% of its overlap on
        convergence alone, so k = 0 and a fixed camera has no tolerance. Not
        a negative angle, which a caller comparing an error against it would
        read as "always outside" for the wrong reason.

        ``angle_tolerance_deg`` passes ``k`` on unfloored, so that the one
        guard is ``angle_check``'s; the mutant was made there, in the private
        copy. Mutant "no floor on k" (``angle_check`` no longer reads a k at or
        below zero as no tolerance) failed:
            AssertionError: assert -0.6414479571824361 == 0.0
        """
        assert framing.convergence_share(spec(1, 4, 0.05, 75.0)) > 0.5
        assert framing.angle_tolerance_deg(spec(1, 4, 0.05, 75.0)) == 0.0

    def test_no_overlap_means_no_tolerance(self):
        """Control: with no overlap any angle error opens a gap. The share is
        infinite here, and the budget it leaves is none.

        Mutant "a zero strip unguarded" failed:
            ZeroDivisionError: float division by zero
        """
        assert framing.angle_tolerance_deg(spec(2, 2, 0.0, 41.0)) == 0.0
