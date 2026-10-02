# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The re-frame carry: when a moved layout keeps its counts (#189 Revision 2
ruling 3, #187; spec 3.3 and A.5).

THE RULING. The owner's words were "if the re-frame is less than half the
width of the overlap, carry over" the panel counts; otherwise restart them.
``framing.reframe_carry(anchor, new)`` is that sentence as one pure function,
and these tests pin what it measures and against what:

* THE MOVE is the largest angular distance any panel CORNER travels, each
  corner placed at its panel's angle in that panel's own tangent plane with
  ``deproject``. Corners, not centres: a single panel turned 90 degrees keeps
  its centre exactly where it was, and its frames are of another field.
* THE THRESHOLD is ``REFRAME_CARRY_FRACTION`` (0.5) of the overlap width of the
  ANCHOR's grid: the anchor is what the counts started at, so it is what says
  how much a move may spend.
* SOME CHANGES ARE NOT MOVES. Rows or cols changing, the angle crossing
  between "any" and a set value, or another named object, re-anchor whatever
  the corners do.
* NO CAMERA FIELD, NO "A LITTLE". A block with no field recorded has a
  threshold of 0, so only an unchanged geometry keeps its anchor.

Every number here was recomputed with the repo's ``compute_mosaic`` and
``deproject`` before it was pinned, and each agrees with Appendix A.5.

Every test names the mutation of ``catalog/framing.py`` it guards and quotes
the failure that mutation produced, each run in a private copy of server/
(scratchpad s3-G-mut), never in the shared tree. The controls say what must
not change.
"""
from __future__ import annotations

import json
import math

import pytest

from astrodeck.catalog import framing
from astrodeck.flows import identity

ARCMIN = 1.0 / 60.0


def frame(**over) -> dict:
    """The task's 3x2 of 2.0 x 1.33 deg at 25%, Dec 41, angle 30, in the
    anchor's own field names (``identity.anchor_geometry``'s shape). RA is an
    arbitrary sky position; nothing here is site data."""
    base = {"ra_hours": 0.7122, "dec_deg": 41.0, "rotation_deg": 30.0,
            "rows": 2, "cols": 3, "overlap": 0.25,
            "fov_x": 2.0, "fov_y": 1.33}
    base.update(over)
    return base


def single(**over) -> dict:
    """A 1x1 of the same panel."""
    return frame(rows=1, cols=1, **over)


def north(f: dict, arcmin: float) -> dict:
    return {**f, "dec_deg": f["dec_deg"] + arcmin * ARCMIN}


def turned(f: dict, deg: float) -> dict:
    return {**f, "rotation_deg": f["rotation_deg"] + deg}


def test_the_constants_are_the_rulings():
    """``REFRAME_CARRY_FRACTION`` is the owner's "half the width of the
    overlap"; ``DEFAULT_OVERLAP`` is spec 2.4's one default, 25%, as the
    fraction ``compute_mosaic`` takes.

    Mutant "DEFAULT_OVERLAP = 0.15" (the value ``mosaic.ts`` carried) failed:
        assert 0.15 == 0.25
         +  where 0.15 = framing.DEFAULT_OVERLAP
    """
    assert framing.REFRAME_CARRY_FRACTION == 0.5
    assert framing.DEFAULT_OVERLAP == 0.25


class TestThePinnedCases:
    """The 3x2 of 2.0 x 1.33 deg at 25%, Dec 41, angle 30. Its overlap width
    is the rows' 25% of 1.33 deg (19.95'), narrower than the columns' 25% of
    2.0 deg, so it allows 9.975'."""

    def test_the_threshold_is_half_the_narrower_overlap(self):
        """Mutant "the wider strip" (``max`` for ``min`` over the strips that
        exist) failed, the columns' 15' allowed:
            assert 15.0 == 9.975 ± 1.0e-09
        """
        got = framing.reframe_carry(frame(), north(frame(), 1.0))
        assert got["threshold_deg"] / ARCMIN == pytest.approx(9.975, abs=1e-9)

    def test_a_north_shift_of_9_9_arcmin_carries_and_10_1_re_anchors(self):
        """A pure shift north moves every corner by about the shift itself.

        Mutant "threshold doubled" (``REFRAME_CARRY_FRACTION = 1.0``) failed:
            AssertionError: assert (True, 'move') == (False, 'move')
              At index 0 diff: True != False
        """
        under = framing.reframe_carry(frame(), north(frame(), 9.9))
        over = framing.reframe_carry(frame(), north(frame(), 10.1))
        assert under["max_move_deg"] / ARCMIN == pytest.approx(9.89979,
                                                               abs=1e-4)
        assert over["max_move_deg"] / ARCMIN == pytest.approx(10.09978,
                                                              abs=1e-4)
        assert (under["carry"], under["reason"]) == (True, "move")
        assert (over["carry"], over["reason"]) == (False, "move")

    def test_a_turn_of_3_3_deg_carries_and_3_5_deg_re_anchors(self):
        """A turn swings the outer corners of the grid furthest: 3.3 deg moves
        one 9.61', 3.5 deg moves one 10.19'.

        Mutant "the grid's centre is the only pivot" (every panel's corners
        deprojected about the grid's centre instead of the panel's own)
        failed:
            assert 4.148620283394156 == 9.60634 ± 1.0e-04
        """
        under = framing.reframe_carry(frame(), turned(frame(), 3.3))
        over = framing.reframe_carry(frame(), turned(frame(), 3.5))
        assert under["max_move_deg"] / ARCMIN == pytest.approx(9.60634,
                                                               abs=1e-4)
        assert over["max_move_deg"] / ARCMIN == pytest.approx(10.18845,
                                                              abs=1e-4)
        assert under["carry"] is True
        assert over["carry"] is False

    def test_an_east_shift_of_the_same_arc_moves_the_south_corners_further(
            self):
        """What "a 9.9' shift" means: the spec's column is a NORTH shift. The
        same 9.9' of arc toward the east, measured at the grid's centre,
        carries the southern corners 10.23', because a step in RA is a longer
        arc at a lower declination, so it re-anchors. Pinned so that nobody
        "fixes" the rule to make both directions agree: the corners are what
        the frames cover.

        Mutant "centres, not corners" failed on the pinned move, the centres
        travelling 10.08' where the corners travel 10.23':
            assert 10.07655647140787 == 10.23275 ± 1.0e-04
        Its verdict would still have been a re-anchor: the panel centres
        south of the grid's centre move further than the shift as well.
        """
        step_h = (9.9 * ARCMIN) / math.cos(math.radians(41.0)) / 15.0
        east = {**frame(), "ra_hours": frame()["ra_hours"] + step_h}
        got = framing.reframe_carry(frame(), east)
        assert got["max_move_deg"] / ARCMIN == pytest.approx(10.23275,
                                                             abs=1e-4)
        assert got["carry"] is False


def test_a_single_panel_turned_90_degrees_re_anchors():
    """Its centre stays exactly put, and its corners swing 101.9'. Measured at
    the centres, a turned 1x1 would keep counts shot of another field (ruling
    3: "Centres alone would let a 1x1 block turn 90 degrees and keep its
    counts").

    Mutant "centres, not corners" (each panel placed as its centre only)
    failed:
        assert 0.0 == 101.8834 ± 0.001
    """
    got = framing.reframe_carry(single(), turned(single(), 90.0))
    assert got["max_move_deg"] / ARCMIN == pytest.approx(101.8834, abs=1e-3)
    assert got["threshold_deg"] / ARCMIN == pytest.approx(9.975, abs=1e-9)
    assert got["carry"] is False


class TestTheThresholdIsTheAnchors:
    """The anchor is what the counts started at, so its grid says how much a
    move may spend. A threshold read from the NEW grid would let an edit
    raise its own allowance: widen the overlap and a move the anchor never
    allowed carries."""

    def test_widening_the_overlap_cannot_buy_its_own_carry(self):
        """25% -> 35% moves the outer panels 12.82' inward. The anchor allows
        9.975', so it re-anchors; the new grid would have allowed 13.97'.

        Mutant "threshold from the new grid" failed:
            assert 13.965 == 9.975 ± 1.0e-09
        """
        got = framing.reframe_carry(frame(), frame(overlap=0.35))
        assert got["max_move_deg"] / ARCMIN == pytest.approx(12.8198,
                                                             abs=1e-3)
        assert got["threshold_deg"] / ARCMIN == pytest.approx(9.975, abs=1e-9)
        assert got["carry"] is False

    def test_narrowing_the_overlap_is_judged_by_the_anchor_too(self):
        """25% -> 18% moves the outer panels 8.97' outward: under the anchor's
        9.975', so it carries, though the new grid alone allows 7.18'.

        Mutant "threshold from the new grid" failed here the other way:
            assert False is True
        """
        got = framing.reframe_carry(frame(), frame(overlap=0.18))
        assert got["max_move_deg"] / ARCMIN == pytest.approx(8.9728, abs=1e-3)
        assert got["carry"] is True


# Appendix A.5, row by row: (label, anchor, the spec's threshold in arcmin,
# its north shift in arcmin, its turn in degrees, its field change in
# percent). Each figure is written as the spec prints it.
A5 = [
    ("3x2 25% Dec 41", frame(), 9.98, 9.98, 3.43, 6.0),
    ("3x2 25% Dec 75", frame(dec_deg=75.0), 9.98, 9.90, 3.33, 6.0),
    ("3x1 25% Dec 41", frame(rows=1), 15.00, 15.00, 5.50, 9.6),
    ("3x3 15% Dec 41", frame(rows=3, overlap=0.15), 5.99, 5.99, 1.75, 3.1),
    ("2x2 0.9x0.6 25% Dec 41", frame(rows=2, cols=2, fov_x=0.9, fov_y=0.6),
     4.50, 4.50, 4.53, 7.9),
    ("1x1 25% Dec 41", single(), 9.98, 9.98, 7.94, 13.9),
]


def _reach(anchor: dict, move, hi: float) -> float:
    """The smallest pure move of one kind that reaches the threshold, by
    bisection, as A.5 found its last three columns."""
    lo = 0.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if framing.reframe_carry(anchor, move(anchor, mid))["carry"]:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _scaled(f: dict, pct: float) -> dict:
    k = 1.0 + pct / 100.0
    return {**f, "fov_x": f["fov_x"] * k, "fov_y": f["fov_y"] * k}


@pytest.mark.parametrize("label, anchor, thr, shift, turn, field", A5,
                         ids=[row[0] for row in A5])
def test_appendix_a5_is_what_the_code_computes(label, anchor, thr, shift,
                                               turn, field):
    """Each A.5 row recomputed from ``reframe_carry`` itself, to the
    precision the spec prints (half its last digit).

    Mutant "the rows' overlap only" (the width always ``overlap x fov_y``,
    never the columns') failed the single row of three, and only it: every
    other row has rows, or is a 1x1 whose narrower side is its height:
        FAILED ...[3x1 25% Dec 41]
        assert 9.975000000000001 == 15.0 ± 0.005
    """
    got = framing.reframe_carry(anchor, north(anchor, 0.0))
    assert got["threshold_deg"] / ARCMIN == pytest.approx(thr, abs=0.005)
    assert _reach(anchor, north, 30.0) == pytest.approx(shift, abs=0.005 + 1e-3)
    assert _reach(anchor, turned, 20.0) == pytest.approx(turn, abs=0.005 + 1e-3)
    assert _reach(anchor, _scaled, 30.0) == pytest.approx(field,
                                                          abs=0.05 + 1e-3)


class TestChangesThatAreNotMoves:
    def test_a_change_of_rows_or_cols_re_anchors(self):
        """A panel of another grid is another panel, so there is no move to
        measure: ``max_move_deg`` is None and the reason says why.

        Mutant "no grid check" (rows and cols never compared) failed, its
        verdict right for the wrong reason (the 3x3's panels at the 3x2's
        row and column numbers are half a degree away):
            AssertionError: {'cols': 3, 'dec_deg': 41.0, 'fov_x': 2.0,
            'fov_y': 1.33, ...}
            assert (False, 0.502...20593, 'move') == (False, None, 'grid')
              At index 1 diff: 0.5025143790020593 != None
        """
        for new in (frame(rows=3), frame(cols=2), single()):
            got = framing.reframe_carry(frame(), new)
            assert (got["carry"], got["max_move_deg"], got["reason"]) == \
                (False, None, "grid"), new
            assert got["threshold_deg"] / ARCMIN == pytest.approx(9.975)

    def test_any_angle_to_a_set_angle_re_anchors_even_where_nothing_moves(
            self):
        """"Any angle" is laid out at 0 for the measurement, so a 1x1 set to
        exactly 0 has corners exactly where the any-angle ones were. It still
        re-anchors: frames shot at an unknown angle are not frames at PA 0.

        Mutant "no angle-kind check" failed, carried with no move (8.5e-07
        deg is the shared separation's floor for two identical points):
            AssertionError: (-1.0, 0.0)
            assert (True, 8.5377...9e-07, 'move') == (False, None, 'angle')
        """
        for old, new in ((-1.0, 0.0), (None, 0.0), (0.0, -1.0), (15.0, None)):
            got = framing.reframe_carry(single(rotation_deg=old),
                                        single(rotation_deg=new))
            assert (got["carry"], got["max_move_deg"], got["reason"]) == \
                (False, None, "angle"), (old, new)

    def test_two_any_angles_are_one_angle(self):
        """-1, -5 and None all mean "any angle" (``identity`` keys them as one
        geometry, #150), so moving between them is no change at all.

        Mutant "no unchanged rule" failed:
            AssertionError: assert (True, 'move') == (True, 'unchanged')
        """
        for old, new in ((-1.0, -5.0), (None, -1.0)):
            got = framing.reframe_carry(single(rotation_deg=old),
                                        single(rotation_deg=new))
            assert (got["carry"], got["reason"]) == (True, "unchanged")


class TestNoCameraField:
    """S1's single targets carry no field (``fov_x = fov_y = 0``), and so does
    any block framed before MATCH CAMERA. Their threshold is 0."""

    S1 = dict(rows=1, cols=1, fov_x=0.0, fov_y=0.0, rotation_deg=None)

    def test_an_unchanged_geometry_keeps_its_anchor(self):
        """Threshold 0 and move 0: ``0 < 0`` is false, so without the
        unchanged rule every save of every single target would re-anchor it,
        and re-anchoring to the same geometry would at least be harmless; but
        the answer would say "restart" to an operator who changed nothing.

        Mutant "no unchanged rule" (carry only when the move is under the
        threshold) failed:
            Differing items:
            {'carry': False} != {'carry': True}
            {'reason': 'move'} != {'reason': 'unchanged'}
        """
        got = framing.reframe_carry(frame(**self.S1), frame(**self.S1))
        assert got == {"carry": True, "max_move_deg": 0.0,
                       "threshold_deg": 0.0, "reason": "unchanged"}

    def test_any_move_re_anchors(self):
        """One arcsecond north is a move, and with no field there is no
        measure of "a little"."""
        got = framing.reframe_carry(frame(**self.S1),
                                    north(frame(**self.S1), 1.0 / 60.0))
        assert got["threshold_deg"] == 0.0
        # The shared law-of-cosines separation (``coords.angular_sep_deg``)
        # resolves one arcsecond to a few parts in a million, which is far
        # below anything a threshold is compared at.
        assert got["max_move_deg"] / ARCMIN == pytest.approx(1.0 / 60.0,
                                                             rel=1e-4)
        assert (got["carry"], got["reason"]) == (False, "move")

    def test_a_change_that_moves_no_corner_still_re_anchors(self):
        """With no field every corner sits at the centre, so an overlap edit
        moves nothing: a move of 0 against a threshold of 0. It is still a
        change (the overlap is in the key, as it was in S1's), and only an
        unchanged geometry keeps its anchor. This is the case that pins the
        comparison as strict.

        Mutant "carry at the threshold" (``<=`` for ``<``) failed:
            AssertionError: assert (True, 0.0, 'move') == (False, 0.0, 'move')
        """
        got = framing.reframe_carry(frame(**self.S1),
                                    frame(**self.S1, overlap=0.3))
        assert (got["carry"], got["threshold_deg"], got["reason"]) == \
            (False, 0.0, "move")
        # Nothing moved, to the shared law-of-cosines separation's floor: it
        # can answer ~1e-6 deg (a few milliarcseconds) for two identical
        # points, which is why "unchanged" is decided by the key, not by a
        # move of exactly 0.
        assert got["max_move_deg"] == pytest.approx(0.0, abs=1e-6)

    def test_below_the_keys_precision_is_unchanged(self):
        """The anchor is stored at the identity key's precision (1e-6 h of RA,
        0.054"), so a value that differs from it by less keys the same and is
        the same geometry. Otherwise an anchor read back from its own text
        would not equal the geometry it was written from.

        Mutant "no unchanged rule" failed:
            AssertionError: assert (False, 'move') == (True, 'unchanged')
        """
        got = framing.reframe_carry(frame(**self.S1),
                                    {**frame(**self.S1),
                                     "ra_hours": 0.7122 + 3e-7})
        assert (got["carry"], got["reason"]) == (True, "unchanged")


class TestTheAnchorsForms:
    def test_the_stored_text_answers_as_its_geometry(self):
        """The anchor is stored as ``identity``'s canonical JSON; reading it
        back gives the same verdict as the geometry it was written from.

        Mutant "fields crossed in anchor_geometry" (``identity`` reads
        ``fov_x`` from the text's ``fov_y`` and the reverse) failed:
            Differing items:
            {'max_move_deg': 1.1779296643872608} != {'max_move_deg':
            0.16499645361692847}
            {'carry': False} != {'carry': True}
        """
        text = identity.canonical_geometry(
            0.7122, 41.0, 30.0, rows=2, cols=3, overlap=0.25, fov_x=2.0,
            fov_y=1.33)
        for arcmin in (9.9, 10.1):
            assert framing.reframe_carry(text, north(frame(), arcmin)) == \
                framing.reframe_carry(frame(), north(frame(), arcmin))

    def test_a_mosaic_spec_is_a_frame(self):
        """The route's own ``MosaicSpecIn`` is accepted as the new layout.

        Mutant "spec fields swapped" (``fov_x`` read from ``fov_y_deg`` and
        the reverse) failed:
            Differing items:
            {'max_move_deg': 1.1723224637510619} != {'max_move_deg':
            0.16832971593624388}
        """
        spec = framing.MosaicSpecIn(
            ra_hours=0.7122, dec_deg=41.0 + 10.1 * ARCMIN, rows=2, cols=3,
            overlap=0.25, rotation_deg=30.0, fov_x_deg=2.0, fov_y_deg=1.33)
        assert framing.reframe_carry(frame(), spec) == \
            framing.reframe_carry(frame(), north(frame(), 10.1))

    def test_a_named_frame_is_laid_out_where_the_catalogue_puts_it(self):
        """A name-keyed block's anchor holds its canonical identity, grid and
        angle but no coordinates, because its coordinates are the catalogue's
        answer and a planet's moves by the hour (#189 A5). Its shape is
        compared at ``at``, one position for both.

        Mutant "at ignored" (a frame with no coordinates laid out at 0h, 0
        deg) failed:
            Differing items:
            {'max_move_deg': 0.16835094109359636} != {'max_move_deg':
            0.1698074654842341}
        """
        anchor = identity.canonical_name(
            "Jupiter", 30.0, rows=2, cols=3, overlap=0.25, fov_x=2.0,
            fov_y=1.33)
        new = {**frame(), "name": "Jupiter"}
        del new["ra_hours"], new["dec_deg"]
        at = (0.7122, 41.0)
        assert framing.reframe_carry(anchor, turned(new, 3.5), at=at) == \
            framing.reframe_carry(frame(), turned(frame(), 3.5))
        with pytest.raises(ValueError, match="no coordinates"):
            framing.reframe_carry(anchor, turned(new, 3.5))
        # An unchanged named frame needs no layout, so no position either.
        assert framing.reframe_carry(anchor, new)["reason"] == "unchanged"

    def test_another_object_or_kind_re_anchors(self):
        """Another canonical identity is another object, and a name against
        typed coordinates is another kind of key: nothing is measured.

        Mutant "names not compared" failed, Jupiter's counts carried to
        Saturn:
            AssertionError: assert (True, 8.5377...9e-07, 'move') == (False,
            None, 'identity')
        """
        at = (0.7122, 41.0)
        jupiter = {**frame(), "name": "Jupiter"}
        saturn = {**frame(), "name": "Saturn"}
        for old, new in ((jupiter, saturn), (jupiter, frame()),
                         (frame(), jupiter)):
            got = framing.reframe_carry(old, new, at=at)
            assert (got["carry"], got["max_move_deg"], got["reason"]) == \
                (False, None, "identity")

    def test_a_blank_anchor_is_refused(self):
        """No anchor yet is the SAVE's case (the anchor becomes the current
        geometry, spec 3.3); asked here it would have to invent a verdict.

        Mutant "blank anchor not refused" (an empty frame returned) failed:
            KeyError: 'overlap'
        """
        with pytest.raises(ValueError, match="blank"):
            framing.reframe_carry("", frame())

    @pytest.mark.parametrize("drop", [("overlap",), ("ra_hours", "dec_deg")],
                             ids=["no overlap", "no position or name"])
    def test_a_mapping_that_is_not_a_frame_is_refused(self, drop):
        """A mapping with a field missing, or with neither a position nor a
        name, is refused by name before anything reads it, not answered
        with whichever ``KeyError`` the first rule to touch it raises.

        Added by the S3-G verifier: mutant "mapping unchecked" (``_frame``'s
        field check removed) survived every test above, and failed here,
        each row:
            [no overlap] KeyError: 'overlap'
            [no position or name] KeyError: 'ra_hours'
        """
        bad = frame()
        for key in drop:
            del bad[key]
        with pytest.raises(ValueError, match="a frame needs"):
            framing.reframe_carry(frame(), bad)
        # Control: the whole mapping is a frame.
        assert framing.reframe_carry(frame(), frame())["carry"] is True


class TestControls:
    def test_a_layout_compared_with_itself_carries(self):
        """Nothing moved: carried, a move of 0, and the reason says so.

        Mutant "no unchanged rule" failed, the move being the shared
        separation's floor rather than 0:
            AssertionError: assert (True, 8.5377...9e-07, 'move') == (True,
            0.0, 'unchanged')
        """
        for f in (frame(), single(), frame(rows=3, cols=3, overlap=0.15)):
            got = framing.reframe_carry(f, dict(f))
            assert (got["carry"], got["max_move_deg"], got["reason"]) == \
                (True, 0.0, "unchanged")

    def test_the_answer_is_json_plain(self):
        """The route ships it as is: bools, floats, None and a word, and every
        verdict kind survives ``allow_nan=False``, which is how starlette
        renders an answer (one infinity would 500 the whole mosaic).

        Mutant "infinity where no move was measured" (the grid verdict's
        ``max_move_deg`` is ``math.inf`` instead of None) failed:
            ValueError: Out of range float values are not JSON compliant: inf
        """
        at = (0.7122, 41.0)
        for old, new in ((frame(), frame(rows=3)), (frame(), frame()),
                         (frame(), north(frame(), 10.1)),
                         (single(rotation_deg=None), single()),
                         ({**frame(), "name": "Jupiter"}, frame())):
            got = framing.reframe_carry(old, new, at=at)
            assert set(got) == {"carry", "max_move_deg", "threshold_deg",
                                "reason"}
            assert isinstance(got["threshold_deg"], float)
            json.dumps(got, allow_nan=False)


class TestANonFiniteFrameIsNotAMove:
    """A frame holding NaN or an infinity is refused, never measured.

    Measured, it CARRIES: every corner of such a frame deprojects to NaN, and
    the shared separation (``coords.angular_sep_deg``) clamps its cosine with
    ``max(-1.0, min(1.0, nan))``, which is 1.0, so every corner "moves" 0.0
    and the verdict is ``carry: True``. That is a plausible wrong answer, a
    campaign keeping its counts across a geometry nobody can lay out, and
    this module treats a plausible wrong answer as worse than an error. The
    stored anchor text already refuses a non-finite number
    (``identity.anchor_geometry``); a mapping, a ``MosaicSpecIn`` and a
    ``MosaicAnchorIn`` get the same refusal in ``_frame``.

    Added by the S3-G verifier, who found the carry (#324: a NaN rotation
    carried with ``max_move_deg`` 0.0 through the route) and fixed it.
    Mutant "no finite check" (``_finite`` lets every value through)
    failed every row here, for example:
        [rotation_deg-nan] Failed: DID NOT RAISE <class 'ValueError'>
        [fov_x-inf] Failed: DID NOT RAISE <class 'ValueError'>

    CONFIRMED BY S4-SAVE (#324), in a private copy of server/:

    * Mutant "the frame skips _finite" (``_frame`` returns its model and
      its mapping frames without ``_finite``) turned all nine of this
      class's frame cases red, S4-SAVE's model case included; eight read

          E   Failed: DID NOT RAISE <class 'ValueError'>

      and [rotation_deg-inf] was refused by the arithmetic instead, which
      is not the refusal the route answers as a 422:

          E   ValueError: math domain error
          E   AssertionError: Regex pattern did not match.
          E     Expected regex: 'not finite'
          E     Actual message: 'math domain error'

    * Mutant "MosaicAnchorIn allows inf/nan" (its ``model_config``
      removed) left the frame cases green, as it must: ``_frame`` still
      refuses. It is graded by S4-SAVE's model case below, red on every
      row, and by the route's two anchor rows
      (test_framing_route_reframe.py).
    """

    @pytest.mark.parametrize("field, value", [
        ("rotation_deg", math.nan), ("rotation_deg", math.inf),
        ("fov_x", math.inf), ("fov_y", math.nan), ("ra_hours", math.nan),
        ("dec_deg", math.nan), ("overlap", math.nan)])
    def test_either_side_is_refused(self, field, value):
        bad = {**frame(), field: value}
        with pytest.raises(ValueError, match="not finite"):
            framing.reframe_carry(frame(), bad)
        with pytest.raises(ValueError, match="not finite"):
            framing.reframe_carry(bad, frame())

    def test_a_spec_is_checked_too(self):
        """``MosaicSpecIn`` takes NaN and an infinite field (pydantic's
        default), so the model form is checked like the mapping."""
        spec = framing.MosaicSpecIn(
            ra_hours=0.7122, dec_deg=41.0, rows=2, cols=3, overlap=0.25,
            rotation_deg=math.nan, fov_x_deg=2.0, fov_y_deg=1.33)
        with pytest.raises(ValueError, match="not finite"):
            framing.reframe_carry(frame(), spec)

    def test_control_any_angle_is_not_a_number_to_check(self):
        """None is "any angle", not a missing number: it passes the check."""
        got = framing.reframe_carry(single(rotation_deg=None),
                                    single(rotation_deg=None))
        assert (got["carry"], got["reason"]) == (True, "unchanged")

    def test_an_anchor_model_is_checked_too(self):
        """The ``MosaicAnchorIn`` branch of ``_frame`` refuses a NaN as the
        spec branch does. The model's own ``allow_inf_nan=False`` stops one
        at the request, so this reaches the branch around it, as a caller
        that builds the model without validating it would (#324, added by
        S4-SAVE: nothing graded this branch's ``_finite`` on its own).

        RED under mutant "the frame skips _finite" (``_frame`` returns both
        its model and its mapping frames unchecked), observed:

            E       Failed: DID NOT RAISE <class 'ValueError'>
        """
        anchor = framing.MosaicAnchorIn.model_construct(
            ra_hours=0.7122, dec_deg=41.0, rows=2, cols=3, overlap=0.25,
            rotation_deg=math.nan, fov_x_deg=2.0, fov_y_deg=1.33)
        with pytest.raises(ValueError, match="not finite"):
            framing.reframe_carry(anchor, frame())

    @pytest.mark.parametrize("field, value", [
        ("rotation_deg", math.nan), ("rotation_deg", math.inf),
        ("rotation_deg", -math.inf), ("fov_x_deg", math.inf),
        ("fov_y_deg", math.inf)])
    def test_the_anchor_model_refuses_nan_and_infinity(self, field, value):
        """``MosaicAnchorIn`` refuses a non-finite number itself, so the
        route answers 422 before ``_frame`` is reached (#324, added by
        S4-SAVE: only the route graded this, through the request).

        THE ROWS ARE THE FIELDS ONLY ``allow_inf_nan`` GUARDS. A bounded
        field refuses a NaN or an infinity by its bound whatever the config
        (NaN fails ``ge``; ``ra_hours``, ``dec_deg``, ``overlap`` and a
        negative field are all bounded), and a first draft with an
        ``ra_hours`` NaN and a ``fov_y_deg`` of minus infinity stayed green
        under the mutant below for exactly that reason.

        RED under mutant "MosaicAnchorIn allows inf/nan" (its
        ``model_config`` removed), observed on every row, for example
        [rotation_deg-nan]:

            E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
        """
        from pydantic import ValidationError
        good = dict(ra_hours=0.7122, dec_deg=41.0, rows=2, cols=3,
                    overlap=0.25, rotation_deg=30.0, fov_x_deg=2.0,
                    fov_y_deg=1.33)
        framing.MosaicAnchorIn(**good)      # control: the finite one is taken
        with pytest.raises(ValidationError):
            framing.MosaicAnchorIn(**{**good, field: value})


class TestAnAnchorThatRecordsAField:
    """A COMPLETED anchor (#351, S4 orchestrator ruling 4) keys the field of
    none it was anchored with and records the camera's field beside it
    (``identity.complete_anchor``). ``reframe_carry`` lays it out with the
    recorded field and takes its threshold from it, so from then on a move
    is judged against the field the camera has. Completing is the caller's
    (the save's and the route's), never this function's.

    The panel is the module's 1x1 recorded at 1.3 x 0.9 deg: threshold
    0.5 x 0.25 x 0.9 = 0.1125 deg; 3' north moves every corner 0.0500 deg.
    """

    BLANK = identity.canonical_geometry(0.7122, 41.0, 30.0)
    FIELD = dict(fov_x=1.3, fov_y=0.9)

    def completed(self) -> str:
        text = identity.complete_anchor(self.BLANK, identity.canonical_geometry(
            0.7122, 41.0, 30.0, fov_x=1.3, fov_y=0.9))
        assert text is not None, "premise: the field completes the anchor"
        return text

    def test_against_the_field_it_records_it_is_unchanged(self):
        got = framing.reframe_carry(self.completed(), single(**self.FIELD))
        assert got == {"carry": True, "max_move_deg": 0.0,
                       "threshold_deg": 0.5 * 0.25 * 0.9,
                       "reason": "unchanged"}

    def test_a_nudge_is_measured_against_the_recorded_field(self):
        """RED under mutant "threshold from the anchor's zero field after
        completion" (the threshold read from the keyed field, the corners
        still laid out with the recorded one), observed:

            E   assert (False, 0.0) == (True, 0.1125)
            E     At index 0 diff: False != True
        """
        got = framing.reframe_carry(self.completed(),
                                    north(single(**self.FIELD), 3.0))
        assert got["max_move_deg"] == pytest.approx(0.05, abs=1e-5)
        assert (got["carry"], got["threshold_deg"]) == (True, 0.1125)

    def test_the_corners_are_laid_out_with_the_recorded_field(self):
        """Against a frame with no field, every corner of the recorded
        1.3 x 0.9 deg panel comes back to the centre: the half-diagonal,
        0.79 deg, is the move. Laid out with the keyed field of none, it
        would be 0.

        RED under mutant "a recorded field is not laid out" (``_laid_out``
        returns every frame as it is), observed (pytest's plus-minus sign
        written ``+-``):

            E   assert 0.0 == 0.7905 +- 1.0e-04
        """
        got = framing.reframe_carry(self.completed(), single(
            fov_x=0.0, fov_y=0.0))
        assert got["max_move_deg"] == pytest.approx(0.7905, abs=1e-4)
        assert got["carry"] is False

    def test_the_mapping_form_answers_as_the_text(self):
        """``anchor_geometry``'s answer handed back as a mapping, recorded
        pair and all, is the same frame as the text."""
        held = identity.anchor_geometry(self.completed())
        assert "recorded_fov_x" in held, "premise: the pair is read back"
        now = north(single(**self.FIELD), 3.0)
        assert framing.reframe_carry(held, now) == framing.reframe_carry(
            self.completed(), now)

    def test_half_a_recorded_pair_is_refused(self):
        """RED under mutant "half a pair laid out" (``_laid_out`` without its
        pair check), a KeyError escaping instead of the refusal:

            E   KeyError: 'recorded_fov_y'
        """
        held = identity.anchor_geometry(self.completed())
        held.pop("recorded_fov_y")
        with pytest.raises(ValueError, match="a recorded field is a pair"):
            framing.reframe_carry(held, single(**self.FIELD))

    def test_control_this_function_never_completes(self):
        """An anchor with no field against the same geometry with a field is
        judged as no field, threshold 0: the completion is the save's and the
        route's (``identity.completes``), so 3.3's "only an unchanged
        geometry keeps its anchor" still holds here."""
        got = framing.reframe_carry(self.BLANK, single(**self.FIELD))
        assert (got["carry"], got["threshold_deg"], got["reason"]) == \
            (False, 0.0, "move")
