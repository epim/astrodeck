"""Rank the catalog by tonight's visibility for the beginner picker (NOV-3).

Pure ranking only — the astropy ephemeris is computed upstream by
visibility.compute_night and passed in as night dicts. Difficulty is a TAG, not a
ranking input (design spec §1.3): we rank purely by tonight's best-window altitude.
"""
from __future__ import annotations


def tonight_score(night: dict) -> float:
    """Higher = better tonight. Targets that never clear the alt limit sink below
    every riser but stay orderable by how high they get."""
    if night.get("never_rises_above_limit"):
        return -100.0 + float(night.get("transit_alt", 0.0))
    bw = night.get("best_window")
    if bw:
        return float(bw["mean_alt"])
    return float(night.get("transit_alt", 0.0))


def rank_picks(picks: list[dict]) -> list[dict]:
    """Best window first; tie-break brighter (lower mag) first."""
    return sorted(picks, key=lambda p: (-p["score"], p["mag"]))
