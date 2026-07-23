"""PRO-1 calibration matcher — pure predicates + coverage (no I/O).

The AUTHORITATIVE match rule (mirrored, tested, in the TS pre-flight). A light is
covered when a DARK master matches (gain/offset/binning exact, exposure within a
percentage tolerance, temp within a °C tolerance — temp skipped when unknown on
either side) AND a FLAT master matches (filter/binning/gain exact; flat exposure
is auto-solved to ADU so it is not an identity). Bias is optional in v1."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchTolerance:
    exposure_tol_pct: float = 5.0
    temp_tol_c: float = 2.0


@dataclass(frozen=True)
class LightNeed:
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str


@dataclass(frozen=True)
class MasterRecord:
    id: str
    frame_type: str        # DARK|BIAS|FLAT
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str
    frame_count: int
    path: str
    built_ts: float


@dataclass(frozen=True)
class Gap:
    filter: str
    exposure_s: float
    gain: int
    binning: int
    missing: tuple[str, ...]   # subset of ("dark", "flat")


def _within_pct(a: float, b: float, pct: float) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) <= abs(b) * (pct / 100.0)


def _temp_ok(need_t: float | None, master_t: float | None, tol_c: float) -> bool:
    if need_t is None or master_t is None:
        return True                       # temp not a constraint when unknown
    return abs(need_t - master_t) <= tol_c


def dark_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool:
    return (m.frame_type == "DARK" and m.gain == need.gain and m.offset == need.offset
            and m.binning == need.binning
            and _within_pct(need.exposure_s, m.exposure_s, tol.exposure_tol_pct)
            and _temp_ok(need.temp_c, m.temp_c, tol.temp_tol_c))


def flat_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool:
    return (m.frame_type == "FLAT" and m.filter == need.filter
            and m.binning == need.binning and m.gain == need.gain)


def best_master(need: LightNeed, masters: list[MasterRecord], tol: MatchTolerance,
                frame_type: str) -> MasterRecord | None:
    """Closest matching master of ``frame_type`` (DARK ranked by |Δexposure| then
    |Δtemp|, most frames as tiebreak), or None."""
    pred = {"DARK": dark_matches, "FLAT": flat_matches}[frame_type]
    cands = [m for m in masters if pred(need, m, tol)]
    if not cands:
        return None

    def rank(m: MasterRecord):
        dexp = abs(need.exposure_s - m.exposure_s)
        dtemp = (0.0 if (need.temp_c is None or m.temp_c is None)
                 else abs(need.temp_c - m.temp_c))
        return (dexp, dtemp, -m.frame_count)

    return sorted(cands, key=rank)[0]


def coverage_for(needs: list[LightNeed], masters: list[MasterRecord],
                 tol: MatchTolerance) -> list[Gap]:
    """One Gap per uncovered need. ``[]`` when ``masters`` is empty (no nag)."""
    if not masters:
        return []                          # empty library never nags
    gaps: list[Gap] = []
    for n in needs:
        missing: list[str] = []
        if best_master(n, masters, tol, "DARK") is None:
            missing.append("dark")
        if best_master(n, masters, tol, "FLAT") is None:
            missing.append("flat")
        if missing:
            gaps.append(Gap(filter=n.filter, exposure_s=n.exposure_s, gain=n.gain,
                            binning=n.binning, missing=tuple(missing)))
    return gaps
