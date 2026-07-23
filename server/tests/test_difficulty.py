"""Difficulty heuristic (NOV-3): mag + size -> surface brightness -> tier."""
from astrodeck.catalog.difficulty import (
    surface_brightness_mag, difficulty_score, tier_from_score, difficulty_for,
    CURATED,
)


def test_surface_brightness_disk_model():
    # sb = mag + 2.5*log10(pi*(size/2)^2). M42: 4.0 mag, 85' -> ~13.4 mag/arcmin^2.
    assert abs(surface_brightness_mag(4.0, 85.0) - 13.4) < 0.1
    assert abs(surface_brightness_mag(3.4, 190.0) - 14.5) < 0.1


def test_surface_brightness_zero_size_is_integrated_mag():
    # Degenerate size can't take a log — fall back to the integrated mag.
    assert surface_brightness_mag(8.0, 0.0) == 8.0


def test_bright_large_heroes_are_easy():
    for mag, size in [(4.0, 85.0), (3.4, 190.0), (1.6, 110.0), (5.8, 20.0)]:
        assert tier_from_score(difficulty_score(mag, size)) == "easy"


def test_small_faint_galaxies_are_hard():
    assert tier_from_score(difficulty_score(9.4, 10.5)) == "hard"    # M74
    assert tier_from_score(difficulty_score(10.4, 15.8)) == "hard"   # NGC 4565


def test_difficulty_monotonic_in_magnitude():
    # Fainter at equal size never gets EASIER.
    assert difficulty_score(10.0, 10.0) > difficulty_score(5.0, 10.0)


def test_score_clamped_unit_interval():
    assert 0.0 <= difficulty_score(1.0, 200.0) <= 1.0
    assert 0.0 <= difficulty_score(14.0, 1.0) <= 1.0


def test_curated_override_wins_and_marks_source():
    d = difficulty_for("IC 434", 7.3, 60.0)   # Horsehead pinned hard
    assert d["tier"] == "hard"
    assert d["source"] == "curated"
    assert d["score"] is None
    assert set(CURATED) >= {"IC 434", "NGC 1499", "M33", "M101"}


def test_heuristic_row_reports_source_and_sb():
    d = difficulty_for("M42", 4.0, 85.0)
    assert d["tier"] == "easy"
    assert d["source"] == "heuristic"
    assert abs(d["surface_brightness"] - 13.4) < 0.1
    assert 0.0 <= d["score"] <= 1.0
