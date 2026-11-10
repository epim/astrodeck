# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 8.5 (#1145): a frame that is part behind a tree is measured by region.

Star detection, the cloud score and the no-light check each estimated one
number over the whole frame, and a frame holding two populations (a lit sky
and a dark foreground) is described by none. The cases below pin what changed
and what did not:

* ``detect_stars`` measures a frame block by block when it is handed the
  obstruction ``find_obstruction`` found, so the lit half of a half-tree frame
  finds the stars it would find alone and the edge raises no false ones. Called
  without one it is the whole-frame detector it always was.
* ``cloud_score`` measures the lit blocks only when ``find_obstruction`` finds
  an obstruction, re-detects its stars block by block, and says so
  (``obstructed``, ``lit_fraction``). Every frame of one population scores
  exactly as it did.
* ``solve.light.classify`` keeps its kinds and names a partly lit NO_LIGHT or a
  partly dark CLOUD in its ``why``.

The frames are synthetic and seeded; none comes from a real rig frame. Each
fixture was written to a PNG and looked at before any statistic was trusted on
it (a lit half with stars over a smooth dark half, a treeline over a lit sky,
a bright patch among stars, a glow with no stars).

MUTANT RUN, 2026-11-10, from a byte backup of ``astrodeck/imaging/stars.py``,
restored and sha256-verified each time: whole-frame statistics again,
``region_stats`` made to return one block for every frame (the ``grid``
expression changed to ``grid = 1``). Twelve cases go red, among them, verbatim:

    test_the_half_obstructed_frame_is_scored_on_its_lit_half_and_says_so
    E   AssertionError: assert False                 (assert result.obstructed)
    test_a_half_obstructed_frame_with_a_few_lit_stars_is_obstructed
    E   AssertionError: assert <FrameClass.UNKNOWN: 'unknown'> is <FrameClass.OBSTRUCTED: 'obstructed'>
    test_a_half_blocked_frame_finds_the_stars_its_lit_half_would_find_alone
    E   assert None is not None                      (no obstruction found)
    test_a_frame_mostly_behind_a_tree_says_it_is_partly_lit
    E   AssertionError: assert '' == 'part of the ...and not a cap'

TWO MORE MUTANTS, same procedure, each red in exactly the case named for it:
the core-density question in ``find_obstruction`` made ``if False`` goes red in
``test_a_bright_patch_among_stars_is_not_an_obstruction``; ``core = dark`` (the
stars beside the edge counted against it) in
``test_stars_beside_the_edge_in_a_block_that_reads_dark_do_not_refuse_it``
(``assert None is not None``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging import stars
from astrodeck.imaging.clouds import cloud_score
from astrodeck.imaging.frame_class import FrameClass, classify
from astrodeck.imaging.stars import detect_stars, find_obstruction, region_stats
from astrodeck.sequence.cloudstate import verdict_from_info
from astrodeck.solve import light
from astrodeck.solve.light import Reference

# APPENDED, as the other fixture and tool helpers are.
_HERE = Path(__file__).resolve().parent
for _extra in (_HERE / "fixtures" / "frame_class", _HERE.parents[1] / "tools"):
    if str(_extra) not in sys.path:
        sys.path.append(str(_extra))
import no_light_audit as audit  # noqa: E402
import synth_frames as synth  # noqa: E402

SIZE = synth.SIZE
REFERENCE = Reference(level=synth.BLACK_LEVEL, kind=light.DARK_MASTER)

#: Where the half-tree frames' edge sits, and how many stars its lit half holds.
HALF_EDGE_ROW = 500
HALF_STARS = 60


def half_tree_frame(seed: int = 801, stars_in_sky: int = HALF_STARS) -> np.ndarray:
    """About half of the view behind a straight tree line, with the sky above
    it at 600 ADU: ``synth.obstructed_frame`` with no wobble."""
    return synth.obstructed_frame(seed=seed, stars=stars_in_sky,
                                  edge_row=HALF_EDGE_ROW, wobble=0)


def faint_sky_and_tree(edge_row: int, seed: int = 802, count: int = 80
                       ) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """``(frame, same sky with no tree, star positions)``: a 600 ADU sky with
    ``count`` stars from 150 to 4000 ADU, faint ones included, above ``edge_row
    - 40``; the frame has the dark surface below ``edge_row`` as a hard step."""
    rng = np.random.default_rng(seed)
    sky = rng.normal(600.0, 14.0, (SIZE, SIZE))
    dark = rng.normal(synth.BLACK_LEVEL, synth.BLACK_NOISE, (SIZE, SIZE))
    yy, xx = np.mgrid[-8:9, -8:9]
    psf = np.exp(-(xx ** 2 + yy ** 2) / (2 * synth.STAR_SIGMA ** 2))
    where = []
    for _ in range(count):
        x = int(rng.integers(12, SIZE - 12))
        y = int(rng.integers(12, edge_row - 40))
        amplitude = float(np.exp(rng.uniform(np.log(150.0), np.log(4000.0))))
        sky[y - 8:y + 9, x - 8:x + 9] += amplitude * psf
        where.append((x, y))
    framed = sky.copy()
    framed[edge_row:] = dark[edge_row:]
    return (np.clip(np.rint(framed), 0, 65535).astype(np.uint16),
            np.clip(np.rint(sky), 0, 65535).astype(np.uint16), where)


def _found(found: list, where: list[tuple[int, int]]) -> int:
    """How many of the star positions ``where`` have a detection within 2 px."""
    return sum(1 for x, y in where
               if any(abs(s.x - x) < 2 and abs(s.y - y) < 2 for s in found))


# ------------------------------------------------------------------ region_stats

@pytest.mark.parametrize("shape", [(256, 256), (263, 301), (255, 257), (64, 64)],
                         ids=["even", "remainder", "odd", "one-region"])
def test_the_histogram_median_is_numpys_median_to_the_last_bit(shape):
    """A 16-bit block's median and MAD come from its histogram, for speed; they
    must be the numbers ``np.median`` gives, half-integers included."""
    rng = np.random.default_rng(803)
    frame = rng.normal(900.0, 30.0, shape).clip(0, 65535).astype(np.uint16)
    got = region_stats(frame)
    expected = region_stats(frame.astype(np.float64))
    assert np.array_equal(got.median, expected.median)
    assert np.array_equal(got.noise, expected.noise)
    for row in range(got.grid):
        for col in range(got.grid):
            top, bottom, left, right = got.bounds(row, col, shape)
            block = frame[top:bottom, left:right].astype(np.float64)
            assert got.median[row, col] == np.median(block)


def test_every_pixel_is_in_a_block_even_when_the_frame_does_not_divide():
    frame = np.zeros((263, 301), dtype=np.uint16)
    regions = region_stats(frame)
    covered = np.zeros(frame.shape, dtype=int)
    for row in range(regions.grid):
        for col in range(regions.grid):
            top, bottom, left, right = regions.bounds(row, col, frame.shape)
            covered[top:bottom, left:right] += 1
    assert (covered == 1).all()
    assert regions.block_of(262, 300) == (regions.grid - 1, regions.grid - 1)


def test_a_frame_too_small_for_blocks_is_one_region_the_whole_frame_answer():
    frame = synth.stars_frame()[:100, :100]
    regions = region_stats(frame)
    assert regions.grid == 1
    assert regions.median[0, 0] == np.median(frame)


# ----------------------------------------------------------------- detect_stars

def test_a_half_blocked_frame_finds_the_stars_its_lit_half_would_find_alone():
    """With half the view dark, the frame's own MAD is the width of the step
    between the two halves, and a threshold at five of those sits above every
    faint star. Measured against the whole-frame estimate: 19 of 80 found."""
    framed, alone, where = faint_sky_and_tree(HALF_EDGE_ROW)
    assert _found(detect_stars(alone), where) >= 78
    obstruction = find_obstruction(framed, detect_stars(framed))
    assert obstruction is not None
    assert _found(detect_stars(framed, obstruction=obstruction), where) >= 78


def test_a_treeline_across_the_lit_sky_raises_no_false_star():
    """The tree over 87 % of the view puts the whole-frame median in the dark
    and the lit sky's noise above the frame's; the sky's own noise peaks and
    the cut-outs the edge crosses then pass for stars. Every star this fixture
    draws stands at least 1500 ADU over the 600 ADU sky, so a detection below
    that is false."""
    for seed in (303, 304, 305):
        frame = synth.obstructed_frame(seed=seed, stars=12, edge_row=130,
                                       wobble=40)
        obstruction = find_obstruction(frame, detect_stars(frame))
        assert obstruction is not None
        framed = detect_stars(frame, obstruction=obstruction)
        assert len(framed) >= 10, (seed, len(framed))
        assert all(s.peak > 600.0 + 1500.0 for s in framed), (
            seed, sorted(round(s.peak) for s in framed))


def test_a_frame_of_one_population_detects_what_the_whole_frame_estimate_did():
    """No change for a frame with no foreground: the star field fixture finds
    the same 117 stars the whole-frame estimate found on it (counted on the
    tree before this change)."""
    assert len(detect_stars(synth.stars_frame())) == 117


# --------------------------------------------------------------------- the cloud

def test_the_half_obstructed_frame_is_scored_on_its_lit_half_and_says_so():
    """The mutant's case. The sky half is clear (60 stars on half a megapixel);
    over the whole frame the same figures read 10x noise, which is neither the
    sky nor the tree."""
    result = cloud_score(half_tree_frame())
    assert result.obstructed
    assert 0.45 < result.lit_fraction < 0.55
    assert not result.cloudy
    assert result.contrast > 100
    assert result.bright_stars >= 55
    assert result.bright_density > 100
    assert "obstructed" in result.reason and "clear" in result.reason


def test_the_obstruction_covers_the_dark_half_and_no_star_of_the_sky():
    frame = half_tree_frame()
    found = detect_stars(frame)
    obstruction = find_obstruction(frame, found)
    assert obstruction is not None
    # The edge is in the fourth block row, which is mostly sky and so stays lit.
    assert obstruction.dark[:4].sum() == 0
    assert obstruction.dark[4:].all()
    assert not any(obstruction.covers(s) for s in found)
    assert obstruction.lit_pixels(frame.astype(np.float64)).size == (
        4 * obstruction.regions.block_h * SIZE)


def test_stars_handed_in_give_the_same_score_as_stars_found():
    frame = half_tree_frame()
    assert (cloud_score(frame, stars=detect_stars(frame)).to_dict()
            == cloud_score(frame).to_dict())


def test_an_obstructed_score_carries_two_more_keys_and_the_cloud_state_still_reads_it():
    published = cloud_score(half_tree_frame()).to_dict()
    assert published["obstructed"] is True
    assert 0.0 < published["lit_fraction"] < 1.0
    assert {"cloudy", "score", "bright_stars", "bright_density", "contrast",
            "reason"} <= set(published)
    cloudy, score, reason = verdict_from_info({"cloud": published})
    assert (cloudy, score, reason) == (published["cloudy"], published["score"],
                                       published["reason"])


#: What ``cloud_score(...).to_dict()`` said for each frame of one population
#: before this change, from the tree at the WP's base commit. A stored or
#: published score must not move because a foreground test was added.
UNCHANGED = {
    "stars_frame": {"cloudy": False, "score": 0.0, "bright_stars": 117,
                    "bright_density": 111.58, "contrast": 372.3,
                    "reason": "clear (117 bright stars, 372x noise)"},
    "no_light_frame": {"cloudy": True, "score": 0.943, "bright_stars": 0,
                       "bright_density": 0.0, "contrast": 3.1,
                       "reason": "cloudy: 0 bright stars, low contrast (3x noise)"},
    "cloud_frame": {"cloudy": True, "score": 0.963, "bright_stars": 0,
                    "bright_density": 0.0, "contrast": 2.1,
                    "reason": "cloudy: 0 bright stars, low contrast (2x noise)"},
}


@pytest.mark.parametrize("name", sorted(UNCHANGED))
def test_a_frame_of_one_population_scores_exactly_as_before(name):
    result = cloud_score(getattr(synth, name)())
    assert not result.obstructed and result.lit_fraction == 1.0
    assert result.to_dict() == UNCHANGED[name]


@pytest.mark.parametrize("name", ["stars_frame", "no_light_frame", "cloud_frame",
                                  "ramp_frame", "uniform_dim_frame",
                                  "cloud_bank_frame"])
def test_no_frame_of_one_population_is_obstructed(name):
    """A glow, a dark frame, a gradient, a star field, and a bright starless
    bank against a clear window (a cloud, not a foreground: its stars are on
    the dark side)."""
    frame = getattr(synth, name)()
    assert find_obstruction(frame, detect_stars(frame)) is None


def bright_patch_among_stars(seed: int = 804) -> np.ndarray:
    """A galaxy-like case for the block geometry: stars all over a 400 ADU sky
    and one smooth 900 ADU patch (3 x 3 blocks) with a sharp edge. The geometry
    alone reads the patch as a lit sky and the rest as a dark region; the
    stars say it is the other way about, because the "dark" side holds them."""
    rng = np.random.default_rng(seed)
    sky = rng.normal(400.0, 12.0, (SIZE, SIZE))
    sky[:384, :384] = rng.normal(900.0, 12.0, (384, 384))
    yy, xx = np.mgrid[-8:9, -8:9]
    psf = np.exp(-(xx ** 2 + yy ** 2) / (2 * synth.STAR_SIGMA ** 2))
    for _ in range(100):
        x, y = (int(v) for v in rng.integers(12, SIZE - 12, 2))
        sky[y - 8:y + 9, x - 8:x + 9] += float(rng.uniform(1500, 12000)) * psf
    return np.clip(np.rint(sky), 0, 65535).astype(np.uint16)


def test_a_bright_patch_among_stars_is_not_an_obstruction():
    """The geometry fires and the stars refuse it, so the frame is measured as
    a whole, as it always was. RED if the core's star density is not asked."""
    frame = bright_patch_among_stars()
    found = detect_stars(frame)
    assert stars.dark_blocks(region_stats(frame)) is not None
    assert find_obstruction(frame, found) is None
    assert len(found) >= 90
    assert not cloud_score(frame, stars=found).obstructed


def test_stars_beside_the_edge_in_a_block_that_reads_dark_do_not_refuse_it():
    """With the edge at row 600 and a 40 row wobble the fifth block row is
    mostly dark and holds stars of the sky just above the edge. They sit in a
    block the edge crosses, not in the core of the dark region."""
    frame = synth.obstructed_frame(seed=305, stars=12, edge_row=600, wobble=40)
    found = detect_stars(frame)
    obstruction = find_obstruction(frame, found)
    assert obstruction is not None
    assert sum(1 for s in found if obstruction.covers(s)) >= 1


def test_the_cloud_score_counts_the_stars_the_whole_frame_estimate_misses():
    """A tree over half the view: the whole-frame detector finds 19 of the 80
    stars the sky holds, so a caller's own detection (the hub's) would read the
    lit half as nearly starless. The score finds the rest itself."""
    framed, _alone, where = faint_sky_and_tree(512)
    handed = detect_stars(framed)
    assert _found(handed, where) < 40
    result = cloud_score(framed, stars=handed)
    assert result.obstructed and not result.cloudy
    assert result.bright_stars >= 70
    assert result.bright_stars == cloud_score(framed).bright_stars


def test_the_standard_error_constant_is_the_one_solve_light_uses():
    assert stars.MEDIAN_SE_FACTOR == light.MEDIAN_SE_FACTOR


# ------------------------------------------------------------------- the class

def test_a_half_obstructed_frame_with_a_few_lit_stars_is_obstructed():
    """Not clear, and not cloud. RED under the mutant: with one block per frame
    nothing is found, and the same frame reads as cloud."""
    frame = half_tree_frame(stars_in_sky=12)
    assert classify(frame) is FrameClass.OBSTRUCTED


# ------------------------------------------------------------- the no-light check

def test_a_frame_mostly_behind_a_tree_says_it_is_partly_lit():
    frame = synth.obstructed_frame(edge_row=130, wobble=10)
    verdict = light.classify(frame, REFERENCE)
    assert verdict.kind == light.NO_LIGHT
    assert verdict.why == light.PARTLY_LIT_WHY


def test_a_frame_65_percent_behind_a_tree_says_part_of_it_is_dark():
    verdict = light.classify(synth.obstructed_frame(), REFERENCE)
    assert verdict.kind == light.CLOUD
    assert verdict.why == light.PARTLY_DARK_WHY


@pytest.mark.parametrize("name,kind", [("no_light_frame", light.NO_LIGHT),
                                       ("cloud_frame", light.CLOUD)])
def test_a_frame_of_one_population_has_no_regional_note(name, kind):
    verdict = light.classify(getattr(synth, name)(), REFERENCE)
    assert verdict.kind == kind
    assert verdict.why == ""


def test_the_evidence_line_of_a_noted_verdict_still_parses():
    verdict = light.classify(synth.obstructed_frame(edge_row=130, wobble=10),
                             REFERENCE)
    reading = audit.parse_line(f"{audit.PREFIX} {verdict.evidence()}")
    assert reading is not None
    assert reading.kind == "no_light"
    assert reading.why == light.PARTLY_LIT_WHY


def test_the_published_cloud_dict_serialises():
    """The preview payload is JSON; the new keys are plain values."""
    json.dumps(cloud_score(half_tree_frame()).to_dict())
