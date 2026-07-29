"""Constellation registration — the translation between two star fields.

Every test here is a failure mode the single-brightest-star anchor it replaces
actually had. The point is not that the new code finds the right shift on clean
data (v1 did too); it is that it still finds the right shift when the brightest
star lies, and that it says so honestly when nothing agrees.
"""
from __future__ import annotations

from astrodeck.imaging.registration import brightest, register
from astrodeck.imaging.stars import Star


def _s(x, y, flux=1000.0):
    return Star(x=x, y=y, flux=flux, hfr=2.0, peak=flux / 10)


#: A field with no accidental symmetry, so a wrong pairing has no support.
FIELD = [_s(100, 100, 5000), _s(300, 140, 4000), _s(180, 320, 3000),
         _s(420, 400, 2500), _s(60, 380, 2000), _s(500, 90, 1500)]


def _shifted(stars, dx, dy):
    return [_s(s.x + dx, s.y + dy, s.flux) for s in stars]


# -------------------------------------------------------------------- basics

def test_brightest_selects_on_flux_not_peak():
    """detect_stars orders by PEAK, and a saturated star's peak is clipped —
    so peak order puts flat-topped blobs ahead of genuinely brighter stars."""
    stars = [Star(x=1, y=1, flux=10.0, hfr=2, peak=9999),   # saturated, dim
             Star(x=2, y=2, flux=9000.0, hfr=2, peak=100)]  # bright, unsaturated
    assert brightest(stars, 1)[0].flux == 9000.0


def test_a_pure_translation_is_recovered_exactly():
    reg = register(FIELD, _shifted(FIELD, 7, -3))
    assert reg is not None
    assert abs(reg.dx - 7) < 1e-6 and abs(reg.dy + 3) < 1e-6
    assert reg.support == len(FIELD)


def test_sub_pixel_translation_is_recovered():
    """The whole reason for the refine step: a half-pixel drift is the normal
    case with dithering off, and rounding it away smears every star."""
    reg = register(FIELD, _shifted(FIELD, 2.4, -1.7))
    assert reg is not None
    assert abs(reg.dx - 2.4) < 0.01 and abs(reg.dy + 1.7) < 0.01


# ------------------------------------------------- the v1 failure modes

def test_the_brightest_star_changing_identity_does_not_move_the_stack():
    """v1's worst failure: two stars of similar flux swap rank between frames,
    the 'anchor' becomes a different star, and the whole stack jumps by their
    separation. Here the top two swap; the constellation is unmoved."""
    cur = _shifted(FIELD, 5, 5)
    cur[0] = _s(cur[0].x, cur[0].y, 4000)      # was 5000 — now second
    cur[1] = _s(cur[1].x, cur[1].y, 5000)      # was 4000 — now first
    reg = register(FIELD, cur)
    assert reg is not None
    assert abs(reg.dx - 5) < 0.01 and abs(reg.dy - 5) < 0.01


def test_the_anchor_star_disappearing_does_not_move_the_stack():
    """Cloud takes the brightest star. v1 re-anchored on whatever was next and
    shifted the stack by the distance between them."""
    cur = _shifted(FIELD, -4, 6)[1:]           # brightest gone
    reg = register(FIELD, cur)
    assert reg is not None
    assert abs(reg.dx + 4) < 0.01 and abs(reg.dy - 6) < 0.01


def test_a_spurious_new_star_does_not_move_the_stack():
    """A hot pixel or cosmic ray brighter than everything real. It proposes a
    translation nothing else agrees with, so it gets one vote and loses."""
    cur = _shifted(FIELD, 3, 3) + [_s(11, 470, 99999)]
    reg = register(FIELD, cur)
    assert reg is not None
    assert abs(reg.dx - 3) < 0.01 and abs(reg.dy - 3) < 0.01


def test_partial_overlap_still_registers():
    """Half the reference stars drifted out of frame; the rest still agree."""
    cur = _shifted(FIELD[:4], 9, 2)
    reg = register(FIELD, cur)
    assert reg is not None
    assert abs(reg.dx - 9) < 0.01 and reg.support >= 3


# ------------------------------------------------------- honest refusal

def test_an_unrelated_field_does_not_register():
    """No shared pattern -> None, so the caller rejects the sub rather than
    stacking it at a guessed offset."""
    other = [_s(11, 470), _s(455, 33), _s(240, 210), _s(90, 15)]
    assert register(FIELD, other) is None


def test_too_few_stars_to_be_a_pattern():
    assert register(FIELD[:2], _shifted(FIELD[:2], 4, 4)) is None
    assert register([], []) is None


def test_min_support_is_honoured():
    """With only two stars in common, three points of agreement is impossible."""
    cur = _shifted(FIELD[:2], 4, 4) + [_s(11, 470), _s(455, 33)]
    assert register(FIELD, cur, min_support=3) is None
    # ...and lowering the bar finds it, so the refusal was the threshold, not
    # a failure to see the pair.
    reg = register(FIELD, cur, min_support=2)
    assert reg is not None and abs(reg.dx - 4) < 0.01


def test_max_shift_rejects_a_real_slew():
    cur = _shifted(FIELD, 300, 200)
    assert register(FIELD, cur, max_shift_px=50) is None
    assert register(FIELD, cur, max_shift_px=500) is not None


def test_tolerance_widens_what_counts_as_agreement():
    """Field rotation across a sub nudges stars off a pure translation. A tight
    tolerance calls that a mismatch; a wider one accepts it."""
    cur = [_s(s.x + 5 + (i * 0.9), s.y + 5, s.flux) for i, s in enumerate(FIELD)]
    assert register(FIELD, cur, tolerance_px=0.3) is None
    reg = register(FIELD, cur, tolerance_px=4.0)
    assert reg is not None and reg.support >= 3


def test_support_and_rms_describe_the_fit():
    reg = register(FIELD, _shifted(FIELD, 1, 1))
    assert reg is not None
    assert reg.support == len(FIELD)
    assert reg.rms < 0.01          # exact translation => near-zero residual
    assert reg.shift == (reg.dx, reg.dy)
