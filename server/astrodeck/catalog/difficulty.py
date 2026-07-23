"""Beginner difficulty rating for catalog targets (NOV-3).

Derives an Easy / Moderate / Hard tag from a target's integrated magnitude and
apparent size via a mean surface-brightness model, with a small curated override
table for famous objects the disk model misreads for a first-timer on an OSC.

Pure — no I/O, no astropy. Imported by objects.search_catalog and the
/api/catalog/tonight route.
"""
from __future__ import annotations

import math

DifficultyTier = str  # "easy" | "moderate" | "hard"

# --- heuristic constants (design spec §1.3; worked vectors in test_difficulty) ---
_EASY_MAG, _HARD_MAG = 6.5, 9.5      # integrated-mag ramp
_EASY_SB, _HARD_SB = 13.0, 15.5      # mean surface-brightness ramp (mag/arcmin^2)
_W_MAG, _W_SB = 0.65, 0.35           # integrated mag dominates; SB is the penalty
_EASY_MAX, _MODERATE_MAX = 0.34, 0.67

# Famous exceptions the mean-SB model misreads for a beginner (id -> tier).
CURATED: dict[str, str] = {
    "IC 434": "hard",      # Horsehead — dark nebula, needs Halpha
    "NGC 1499": "hard",    # California — very low surface brightness on an OSC
    "M33": "moderate",     # Triangulum — large but low SB, a classic letdown
    "M101": "moderate",    # Pinwheel — low-SB face-on spiral
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def surface_brightness_mag(mag: float, size_arcmin: float) -> float:
    """Mean surface brightness (mag/arcmin^2), size as a uniform-disk diameter.

    Larger and/or fainter -> higher (dimmer) number. A non-positive size can't
    take a log, so we fall back to the integrated magnitude (clusters/degenerate).
    """
    if size_arcmin <= 0.0:
        return mag
    area = math.pi * (size_arcmin / 2.0) ** 2  # arcmin^2
    return mag + 2.5 * math.log10(area)


def difficulty_score(mag: float, size_arcmin: float) -> float:
    """0 (dead easy) .. 1 (very hard). Weighted blend of integrated mag and SB."""
    mag_term = _clamp01((mag - _EASY_MAG) / (_HARD_MAG - _EASY_MAG))
    sb = surface_brightness_mag(mag, size_arcmin)
    sb_term = _clamp01((sb - _EASY_SB) / (_HARD_SB - _EASY_SB))
    return _W_MAG * mag_term + _W_SB * sb_term


def tier_from_score(score: float) -> str:
    if score < _EASY_MAX:
        return "easy"
    if score < _MODERATE_MAX:
        return "moderate"
    return "hard"


def difficulty_for(obj_id: str, mag: float, size_arcmin: float) -> dict:
    """Full difficulty block for a target. Curated overrides win over the
    heuristic and carry ``source="curated"`` with a ``None`` score."""
    sb = round(surface_brightness_mag(mag, size_arcmin), 1)
    if obj_id in CURATED:
        return {"tier": CURATED[obj_id], "surface_brightness": sb,
                "source": "curated", "score": None}
    score = difficulty_score(mag, size_arcmin)
    return {"tier": tier_from_score(score), "surface_brightness": sb,
            "source": "heuristic", "score": round(score, 3)}
