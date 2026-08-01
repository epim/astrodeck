"""Noise is not stars.

On 2026-08-01 03:15, under 100% cloud, ``detect_stars`` reported 200 "stars" on
a frame with nothing in it — the native Rust detector reported 2, and the native
detector was right. That count feeds the preview readout, the Bahtinov aid and
the CLOUD DETECTOR, so a cloud detector could see two hundred stars through
solid overcast. It also cost most of a night on 2026-07-31, when "0 stars where
Python finds 130" pointed the blame at the wrong component.

Both fixtures are REAL frames from the same camera at the same exposure (4s,
gain 220, bin 1), one taken under total cloud and one under clear sky. A
synthetic pair would not reproduce this: the failure is caused by the specific
heavy-tailed structure of real sensor noise, which is why a nominal 5-sigma
threshold — 0.3 expected false positives over a megapixel if the noise were
Gaussian — yielded 143.
"""
import json
import pathlib

import numpy as np
import pytest

from astrodeck.imaging.stars import MIN_APERTURE_SNR, detect_stars

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "star_noise"


def _frame(name: str) -> np.ndarray:
    p = FIXTURES / f"{name}.npz"
    if not p.is_file():
        pytest.skip(f"fixture {p.name} not present")
    return np.load(p)["data"].astype(np.float32)


def test_a_blank_overcast_frame_has_no_stars_in_it():
    """THE regression. Anything above zero here is the cloud detector being
    told the sky is clear while it is not."""
    stars = detect_stars(_frame("blank_overcast"))
    assert len(stars) == 0, (
        f"{len(stars)} 'stars' found on a frame with no sources — this is what "
        "made the cloud detector unusable")


def test_a_real_star_field_is_not_thrown_away_with_them():
    """The other half. A noise floor that also removes the sky is not a fix."""
    stars = detect_stars(_frame("star_field"))
    assert len(stars) >= 40, f"only {len(stars)} stars kept on a rich real field"
    hfrs = sorted(s.hfr for s in stars if s.hfr)
    median = hfrs[len(hfrs) // 2]
    # Focused frame: the surviving stars must still measure like stars, not like
    # the box ceiling (see HFR_BOX_CEILING_FRACTION).
    assert 2.0 < median < 6.0, f"median HFR {median:.2f} is not a focused field"


def test_the_two_frames_are_separated_by_a_wide_margin():
    """Not a threshold balanced on a knife edge: the populations do not overlap.

    Measured aperture SNR on these exact frames — blank: median 2.5, 99th
    percentile 5.7; field: median 11.7, 99th percentile 285. The bar sits above
    the whole noise population and below almost all of the real one.
    """
    blank = detect_stars(_frame("blank_overcast"))
    field = detect_stars(_frame("star_field"))
    assert len(blank) == 0 and len(field) > 40
    assert MIN_APERTURE_SNR > 5.7, (
        "the bar must clear the 99th percentile of the measured noise "
        "population, or it is tuned to this frame rather than to the physics")


def test_the_fixtures_are_what_they_claim_to_be():
    """A fixture whose provenance is wrong makes every assertion above a lie."""
    man = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    by_file = {e["file"]: e for e in man["entries"]}
    assert by_file["blank_overcast.npz"]["expected_sources"] == 0
    blank = _frame("blank_overcast")
    field = _frame("star_field")
    assert blank.shape == field.shape == (1024, 1024)
    # The blank frame must be overcast SKY, not a flooded frame. The right
    # discriminator is the MEDIAN, not the peak: a dark attempt through the
    # wheel's supposedly-opaque slot that same morning came back with a median
    # of 65535 (the slot passes light), while this frame sits at normal sky
    # background. Its peak IS saturated — there is a hot pixel in it — and that
    # is precisely the point: a single saturated pixel is not a star, and the
    # detector must say so.
    assert float(np.median(blank)) < 1000, (
        f"blank fixture has a median of {float(np.median(blank)):.0f} — that is "
        "a flooded frame, not overcast sky")
    assert blank.max() >= 60000, (
        "this fixture is expected to contain a saturated hot pixel; if that "
        "ever stops being true, test_a_blank_overcast_frame_has_no_stars_in_it "
        "is no longer proving that hot pixels are rejected")
