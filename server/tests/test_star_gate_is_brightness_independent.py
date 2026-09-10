"""The compactness gate must read a FIELD, not a brightness (GN-05 follow-up).

WHY THIS FILE EXISTS. On 2026-09-07 the resumed NGC 604 run shot donuts 75
steps off focus for half an hour while every autofocus sweep failed, and the
cause was that ``star_size``'s fine path never fired on a FULL frame. The gate
had been calibrated on 512 px crops, where the brightest five detections are
ordinary stars; on the 6248x4176 frames those crops came out of, the brightest
five are near-saturated and the probe's free aperture followed their halos out
past 100 px while the 15 px box stayed where it was. The ratio was therefore a
measurement of how BRIGHT the field was, and it read 1.30-1.37 on clean whole
frames against a threshold of 1.20.

The repair capped the probe aperture at SIZE_TRUNCATION_CAP_PX and moved it
onto the grader's own voting population. ``test_star_size_agrees_with_hfr.py``
grades WHAT the gate decides on the real frames. This file grades the property
that repair was for: the decision is a function of the population's own SHAPE
and of nothing else — not of the gain, not of the sky level, not of how much
light the sources happen to carry.

Measured on the ten real fixtures below, the gate's whole output (detection
count, all three of ``_box_truncation``'s readings, ``compact_star_population``
and ``star_size``) is identical to one part in a million under gains from
x0.0137 to x997 and under sky pedestals of -100 and +5000 ADU. It is identical
because every threshold in the path is expressed in sigma or as a ratio; these
tests are what stops the next absolute number from creeping back in.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.imaging.stars import (
    MIN_SIZE_PX, SIZE_FINE_MAX_BOX_HFR, SIZE_FINE_MAX_PEAK_R,
    SIZE_FINE_MAX_TRUNCATION, _bg_sigma, _box_truncation, _bright_population,
    compact_star_population, detect_stars, focus_size, median_hfr,
    resolved_star_scale, star_size,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ngc604_20260906"


def _frame(name: str) -> np.ndarray:
    return fits.getdata(FIXTURES / f"{name}.fits.gz").astype(np.float64)


def _verdict(img: np.ndarray) -> dict:
    """Everything the gate says about one frame, in one dict.

    Deliberately the WHOLE output and not just the pass/fail: a gate that
    happens to land on the same side for a different reason has still moved,
    and these numbers are what the calibration tables in ``imaging.stars`` are
    written against."""
    bg, sigma = _bg_sigma(img)
    stars = detect_stars(img)
    probe = _box_truncation(img, bg, sigma, stars, min(img.shape) / 2.0)
    fine = compact_star_population(img, stars=stars)
    size = star_size(img)
    return {
        "n_detected": len(stars),
        "truncation": None if probe is None else probe[0],
        "probe_snr": None if probe is None else probe[1],
        "peak_r": None if probe is None else probe[2],
        "box_hfr": (float(np.median([s.hfr for s in _bright_population(stars)]))
                    if stars else None),
        "fine": fine is not None,
        "radius": None if size is None else size.radius,
        "source": None if size is None else size.source,
        "scale": None if size is None else size.scale,
    }


def _assert_same(base: dict, other: dict, what: str) -> None:
    for key, want in base.items():
        got = other[key]
        if isinstance(want, float):
            assert got == pytest.approx(want, rel=1e-6), (
                f"{what}: {key} moved from {want!r} to {got!r} — something in "
                "the gate is reading an absolute ADU level")
        else:
            assert got == want, (
                f"{what}: {key} moved from {want!r} to {got!r} — something in "
                "the gate is reading an absolute ADU level")


# --------------------------------------------------------- (a) real fixtures

#: gain, sky pedestal. x997 is a 10-bit sensor's frame rescaled into a 20-bit
#: container; x0.0137 is the other direction; +5000 ADU is a moonlit sky or an
#: uncalibrated offset. None of them changes a single source's SHAPE.
_REGRADES = [
    ("gain x0.0137", 0.0137, 0.0),
    ("gain x6.3", 6.3, 0.0),
    ("gain x997", 997.0, 0.0),
    ("sky +5000 ADU", 1.0, 5000.0),
    ("sky -100 ADU", 1.0, -100.0),
    ("gain x6.3 on a +12345 sky", 6.3, 12345.0),
]


@pytest.mark.parametrize("name", [
    "clean_R60",        # guided, fresh calibration: round stars      -> fine
    "donut_L60",        # 74 steps off focus: every star a ring       -> pyramid
    "donutfield_L60",   # the same, 1024x1024                         -> pyramid
])
def test_the_gate_reads_one_verdict_per_field_at_any_gain_or_sky_level(name):
    """THE PROPERTY THE 2026-09-07 REPAIR WAS FOR, stated directly.

    A frame scaled by a constant carries exactly the same information: the same
    stars, the same shapes, the same signal-to-noise. Every number the gate
    reports must therefore be the same number. Before the repair this would
    have failed on the full frames of that night, where the probe's free
    aperture turned "how bright is this star" into "how defocused is this
    frame"."""
    img = _frame(name)
    base = _verdict(img)
    assert base["truncation"] is not None, f"{name}: nothing measurable to probe"
    for label, gain, sky in _REGRADES:
        _assert_same(base, _verdict(img * gain + sky), f"{name} at {label}")


def test_a_whole_frames_worth_of_halo_survives_a_regrade():
    """The same property on the fixture that carries the DEFECT'S OWN shape.

    m33field_G60 is 2048x1536 of G_0003 cut to include its bright stars, so the
    halos the old free aperture followed are present. It is the fixture
    ``test_the_discriminator_does_not_read_the_frame_s_SIZE`` uses to show the
    gate no longer reads the frame's size; this shows it does not read the
    frame's LEVEL either. Kept to one gain because a 3 MP frame costs about a
    second per regrade."""
    img = _frame("m33field_G60")
    base = _verdict(img)
    assert base["source"] == "stars"
    _assert_same(base, _verdict(img * 37.0 + 2500.0),
                 "m33field_G60 at gain x37 on a +2500 sky")


def test_the_gate_keeps_its_two_sides_when_the_whole_night_is_regraded():
    """The consequence, as the decision rather than as the numbers: a gain
    change must not move a frame across the gate. Graded on every fixture at
    once so a change that helps one side at the other's expense is visible."""
    keep = {"clean_R60", "m33core_G60", "jump_R60"}
    refuse = {"donut_L60", "staircase_G60", "pedrift_L60", "doubleblob_L60",
              "tail_Ha300"}
    for name in sorted(keep | refuse):
        img = _frame(name)
        want = name in keep
        assert (star_size(img).source == "stars") is want, (
            f"fixture drifted: {name} no longer "
            f"{'takes' if want else 'refuses'} the fine path at gain 1")
        for label, gain, sky in _REGRADES:
            got = star_size(img * gain + sky)
            assert (got.source == "stars") is want, (
                f"{name} at {label} answered from the "
                f"{got.source} path at {got.radius:.2f}px")


# ------------------------------------------- (b) the three regimes, synthetic


def _field(side: int = 512, *, sigma: float | None = None,
           ring_r: float | None = None, n: int = 60, amp: float = 2.0e5,
           galaxy: float = 0.0, galaxy_sigma: float = 40.0, seed: int = 11,
           sky: float = 600.0, noise: float = 12.0) -> np.ndarray:
    """A field of identical sources — sharp Gaussians or rings — optionally
    beside one bright extended object.

    Synthetic because the real frames cannot hold a regime with everything else
    held equal: ``amp`` moves the sources' brightness by a factor of 400 while
    the SHAPE of every one of them stays the same PSF to the last digit."""
    rng = np.random.default_rng(seed)
    img = rng.normal(sky, noise, (side, side))
    if sigma is not None:
        half = int(max(6, sigma * 5))
        yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
        psf = np.exp(-(yy ** 2 + xx ** 2) / (2 * sigma ** 2))
    else:
        half = int(ring_r + 6)
        yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
        psf = np.exp(-((np.hypot(yy, xx) - ring_r) ** 2) / (2 * 2.0 ** 2))
    psf = psf / psf.sum()
    placed: list[tuple[int, int]] = []
    for _ in range(6000):
        if len(placed) >= n:
            break
        y = int(rng.integers(half + 2, side - half - 2))
        x = int(rng.integers(half + 2, side - half - 2))
        if all((y - py) ** 2 + (x - px) ** 2 > (3 * half) ** 2 for py, px in placed):
            placed.append((y, x))
    for (y, x) in placed:
        img[y - half:y + half + 1, x - half:x + half + 1] += amp * psf
    if galaxy > 0.0:
        yy, xx = np.mgrid[0:side, 0:side]
        img += galaxy * np.exp(-((yy - side / 2) ** 2 + (xx - side / 2) ** 2)
                               / (2 * galaxy_sigma ** 2))
    return img


#: x400 of source brightness, from "just above the detection floor" to "railed
#: on a 16-bit sensor". The shapes are identical at every step.
_AMPLITUDES = (5e3, 2e4, 1e5, 5e5, 2e6)


@pytest.mark.parametrize("amp", _AMPLITUDES)
def test_a_sharp_field_takes_the_fine_path_however_much_light_it_carries(amp):
    """REGIME 1: a clean field. The gate must keep it whatever the exposure.

    Not the same test as the gain sweep above: this changes the sources'
    signal-to-noise against a FIXED sky, which is what a longer sub or a
    brighter field actually does, and it is the axis on which a gate built out
    of absolute numbers really does drift. Measured here (median box HFR /
    truncation ratio): 2.65/0.75, 2.08/0.96, 1.91/1.03, 1.87/1.04, 1.86/1.04.
    """
    img = _field(sigma=1.5, n=60, amp=amp)
    size = star_size(img)
    grader, _ = median_hfr(img)
    assert size is not None and grader is not None
    assert size.source == "stars" and size.scale == 1, (
        f"amp {amp:.0f}: a sigma=1.5 field answered from the {size.source} "
        f"path at {size.radius:.2f}px")
    assert size.radius == grader
    assert 1.5 < size.radius < 3.0, (
        f"amp {amp:.0f}: {size.radius:.2f}px is not a sigma=1.5 star "
        "(flux-weighted mean radius 1.88)")


@pytest.mark.parametrize("amp", _AMPLITUDES)
def test_a_donut_field_stays_on_the_pyramid_however_much_light_it_carries(amp):
    """REGIME 3: rings of radius 9 px. The gate must refuse them at every
    brightness, and the pyramid must report the RING and not one arc of it.

    The faintest step is the interesting one: at amp 5e3 ``detect_stars`` finds
    nothing at all (a ring spreads its light too thin to peak), so the fine
    path is refused on the count and the pyramid answers from a binned copy —
    the regime the pyramid was built for."""
    img = _field(ring_r=9.0, n=12, amp=amp)
    size = star_size(img)
    assert size is not None, f"amp {amp:.0f}: a field of rings measured nothing"
    assert size.source == "pyramid", (
        f"amp {amp:.0f}: a field of 9px rings answered from the fine path at "
        f"{size.radius:.2f}px — that is one arc of the ring, not the ring")
    assert 6.0 < size.radius < 12.0, (
        f"amp {amp:.0f}: a 9px ring measured {size.radius:.2f}px")


def test_a_ring_is_refused_for_its_shape_and_not_for_its_faintness():
    """The gates that catch a ring answer different questions, and at least one
    of them must fire at EVERY brightness. Below amp 1e5 the box HFR is what
    gives the ring away (4.57-4.73, against a bar of 3.8); above it, the
    profile's peak radius does too (6-9 px, against a bar of 2). A gate set
    that only worked on bright rings would pass a faint sweep point."""
    for amp in (2e4, 1e5, 5e5):
        img = _field(ring_r=9.0, n=12, amp=amp)
        bg, sigma = _bg_sigma(img)
        stars = detect_stars(img)
        box = float(np.median([s.hfr for s in _bright_population(stars)]))
        probe = _box_truncation(img, bg, sigma, stars, min(img.shape) / 2.0)
        assert probe is not None
        caught = (box >= SIZE_FINE_MAX_BOX_HFR
                  or probe[2] >= SIZE_FINE_MAX_PEAK_R
                  or probe[0] >= SIZE_FINE_MAX_TRUNCATION)
        assert caught, (
            f"amp {amp:.0f}: a 9px ring read box HFR {box:.2f}, truncation "
            f"{probe[0]:.2f}, peak radius {probe[2]:.0f} — every gate let it "
            "through")


#: The galaxy's peak against the stars' own (~14100 ADU for sigma=1.5 at
#: amp=2e5): from a fifteenth of it to twenty-one times it. The stars are the
#: same PSF at every step, so the right answer never moves.
_GALAXY_RAMP = (0.0, 5e3, 1e4, 2e4, 5e4, 1e5, 3e5)


@pytest.mark.parametrize("galaxy", _GALAXY_RAMP)
def test_an_extended_object_beside_stars_does_not_take_the_frame(galaxy):
    """REGIME 2: sixty sigma=1.5 stars with a 40 px-sigma galaxy among them,
    swept from well below the stars' peak to well above it.

    THE BOTTOM OF THE RAMP was always right, and it is the M33 field of
    2026-09-06: G_0003 reads a truncation of 1.11 on the whole 6248x4176 frame
    with eight of its twenty-five probes on the galaxy, and the answer is still
    the stars'.

    THE TOP OF THE RAMP WAS PINNED BROKEN ON 2026-09-08 AND FIXED ON
    2026-09-10. Past a galaxy peak of about 1.4x the stars' the frame's brightest
    detections are the galaxy's own smooth core, chopped into 15 px cells: they
    carry more flux than any star (346137 against 201154 at galaxy 2e4), they
    read the box's saturation HFR (4.03, against the field's 1.88), and they
    take over ``_bright_population``. Gate 2 then crossed at galaxy 2e4 and
    gate 1 at 5e4, and the PYRAMID answered with the galaxy — 49 px at scale 32
    on a frame whose stars are 1.9 px — because a 49 px source carries 10^2
    times a star's flux and ``SIZE_POPULATION_FRAC`` left it the sole voter.

    Refusing the fine path up there is defensible; answering 49 px is not. So
    the pyramid is now bounded by ``resolved_star_scale``: at galaxy 2e4 and
    above, no source may be claimed at more than 12x the 1.88 px median of the
    detections the box sees whole, nothing else in the frame is that big, and
    the stars answer. The number is ``median_hfr``'s at every step of the ramp.

    The two ranking repairs this test's earlier docstring proposed were both
    measured and both refuted; ``imaging.stars``' FINE FIRST block holds the
    numbers.
    """
    img = _field(sigma=1.5, n=60, amp=2e5, galaxy=galaxy)
    size = star_size(img)
    grader, n = median_hfr(img)
    assert size is not None and grader is not None
    assert n >= 60, f"fixture drifted: {n} detections"
    assert size.source == "stars" and size.scale == 1, (
        f"galaxy peak {galaxy:.0f}: answered from the {size.source} path "
        f"at {size.radius:.2f}px (scale {size.scale}) while sixty sharp "
        "stars sat in the same frame")
    assert size.radius == grader
    # The stars are sigma=1.5 (flux-weighted mean radius 1.88) at every step.
    # The box's own reading of them creeps to 4.11 at the top of the ramp,
    # because the galaxy's curvature inside a 15 px cutout is real light; what
    # must never come back is a number about the GALAXY, which is 26x this.
    assert size.radius < 5.0, (
        f"galaxy peak {galaxy:.0f}: {size.radius:.2f}px is not a sigma=1.5 "
        "star field")


def test_the_galaxy_case_is_bounded_by_the_stars_and_not_by_a_threshold():
    """WHY the ramp above holds, at the seam, so a future edit cannot pass it
    by widening a gate instead.

    The fine path is still REFUSED at galaxy 2e4 — truncation 1.26 against the
    1.25 bar, and nothing in the gate set moved on 2026-09-10. What changed is
    that the pyramid may no longer answer with something a dozen times the size
    of the stars this frame resolves.
    """
    img = _field(sigma=1.5, n=60, amp=2e5, galaxy=2e4)
    stars = detect_stars(img)
    scale = resolved_star_scale(img, stars=stars)
    assert scale is not None and 1.5 < scale < 2.5, (
        f"the compact detections median {scale} — not a sigma=1.5 field")
    assert compact_star_population(img, stars=stars) is None, (
        "the gate now ADMITS the galaxy frame, so this test no longer shows "
        "that the bound is what fixed it — re-read the FINE FIRST block")
    bg, sigma = _bg_sigma(img)
    probe = _box_truncation(img, bg, sigma, stars, min(img.shape) / 2.0)
    assert probe is not None and probe[0] >= SIZE_FINE_MAX_TRUNCATION, (
        f"truncation {probe[0]:.3f} now clears {SIZE_FINE_MAX_TRUNCATION}; the "
        "threshold moved, and the 2026-09-08 measurement says no threshold "
        "separates this case")
    assert star_size(img).source == "stars"


# ------------------------------------- (c) what the sweep must never be handed


@pytest.mark.parametrize("name", ["donut_L60", "donutfield_L60"])
def test_a_donut_frame_never_hands_the_sweep_a_small_number(name):
    """The failure the whole fine-first design exists to prevent, at the seam
    the sweep actually calls.

    ``detect_stars`` measures a donut's rim fragments in a 15 px box and reads
    3.94-4.01 px on these two frames — a number that would tell the sweep it
    was in focus and leave it 74 steps out, which is what the night of
    2026-09-07 did. ``focus_size`` must be far above that, and far above
    MIN_SIZE_PX, which only rules out the other end (a noise peak reported as
    half a pixel, #219)."""
    value, n, size = focus_size(_frame(name))
    clean, _, _ = focus_size(_frame("clean_R60"))
    assert value is not None and clean is not None
    assert size is not None and size.source == "pyramid"
    assert value > MIN_SIZE_PX
    assert value >= 3.0, (
        f"{name}: the sweep was handed {value:.2f}px for a field of donuts")
    assert value > 2.0 * clean, (
        f"{name}: {value:.2f}px against {clean:.2f}px in focus — a defocused "
        "frame has to read at least twice a focused one")
    assert n >= 1


def test_the_clean_frames_keep_their_bright_stars():
    """"Most of the bright stars survive the gate" — the other side of the same
    coin, because a gate that refused everything would never be fooled either.

    The fine path votes with ``_bright_population``, the same list
    ``median_hfr`` grades with, so the check is that the whole list votes and
    that its scatter is a star field's: MAD 0.03-0.15 px on these frames, i.e.
    the population agrees with itself to a twentieth of a pixel."""
    for name in ("clean_R60", "m33core_G60", "m33field_G60"):
        img = _frame(name)
        stars = detect_stars(img)
        fine = compact_star_population(img, stars=stars)
        grader, n = median_hfr(img)
        assert fine is not None, f"{name}: the gate refused a focused field"
        assert fine.n_found == len(stars) == n
        assert fine.n_sources == len(_bright_population(stars)) >= 5, (
            f"{name}: {fine.n_sources} of {len(stars)} stars voted")
        assert fine.radius == grader
        assert fine.mad is not None and fine.mad < 0.5, (
            f"{name}: the voting population scatters by {fine.mad} px, which "
            "is not one field of stars")


def test_a_trailed_frame_that_clears_the_gate_answers_from_its_stars():
    """jump_R60 — one ~40 arcsec guide jump and then settle — reads 1.242, the
    closest any must-pass frame comes to the 1.25 bar. It takes the FINE path,
    and a comment in ``imaging.stars`` said for a while that it landed on the
    pyramid.

    That matters beyond the bookkeeping: the fine path answers with the
    grader's own median, so a trailed-but-focused sub is measured the same way
    it is graded, and the autofocus sweep is not told that a guide error was a
    focus error."""
    img = _frame("jump_R60")
    bg, sigma = _bg_sigma(img)
    probe = _box_truncation(img, bg, sigma, detect_stars(img),
                            min(img.shape) / 2.0)
    assert probe is not None
    assert probe[0] < SIZE_FINE_MAX_TRUNCATION
    margin = (SIZE_FINE_MAX_TRUNCATION - probe[0]) / SIZE_FINE_MAX_TRUNCATION
    assert margin < 0.05, (
        f"jump_R60 now clears the bar by {margin:.1%}; it used to be 0.6%, so "
        "either the probe or the fixture has moved and the calibration table "
        "in imaging.stars needs re-measuring")
    size = star_size(img)
    grader, _ = median_hfr(img)
    assert size is not None and size.source == "stars"
    assert size.radius == grader
