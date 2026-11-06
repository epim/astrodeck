# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A horizon the rig learns from the nights it loses to a tree (#599, #1093).

THE DEFECT. A target behind a tree fails to centre. The classifier
(``imaging.frame_class``) now says the solve frame shows an obstruction, and
the hold says so, but the rig then forgets: the site still has no horizon, so
the next night the same target rises through the same tree and holds for the
same 25 minutes. The obstructed frames are the evidence for the horizon the
site lacks.

THE DESIGN. Each obstructed frame is filed under the azimuth bin of the
pointing it failed at, with the altitude it failed at. A frame that centred
(a ``STARS`` verdict) is filed the same way as evidence the other way. When
the frames of a run of neighbouring bins agree, :meth:`HorizonLearner.suggestion`
proposes the line that would have refused them: the drawn horizon raised, never
lowered, to the next five degrees above the highest refusal in each bin.

NOTHING HERE APPLIES ANYTHING (ruling D-07 of the 2026-09-30 backlog plan: the
rig offers a learned mask, the operator accepts it). This module only counts
frames and computes a line. The two routes in ``api.app`` read the suggestion
and, on the operator's accept, write the merged line; no function in this file
touches a config, a location or the engine's floor.

WHAT A REGION IS. A bin is a candidate when it holds a refusal, and is dropped
when a clear frame in it sat at or below the refusal (the frames disagree),
when the drawn horizon already covers it, or when the refusal is above
``MAX_SUGGEST_ALT_DEG`` (a roof or a closed dome, not a tree line; those frames
are not filed at all). Runs of neighbouring candidates (around north included)
become regions, and a region needs ``MIN_REGION_FRAMES`` refusals between its
bins: one misread frame, or two, is not a tree.

PRIVACY. A horizon bin with a time is a latitude oracle once a pointing is
known, so the bins live in this process's memory and nowhere else, are served
only to a principal holding ``view.site_derived`` (the routes), and the one log
line this module writes names counts of bins and frames and no angle. Retention
is ``MAX_NIGHTS`` nights; a restart forgets them, which is the safe direction.

PURE except for that one log line and the lock that makes the engine thread and
the request thread safe against each other.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass

from .catalog.coords import altaz
from .events import bus, night_key
from .imaging.frame_class import FrameClass

#: Azimuth bin width, degrees. Ten degrees is a tree crown seen from a yard:
#: narrower and a held pass of four panels would not fill a bin, wider and one
#: bin would span a tree and the gap beside it.
AZ_BIN_DEG = 10.0

#: A mask altitude is rounded UP to this many degrees. A refusal at pointing
#: altitude A says the obstruction reaches into the frame centred there, so the
#: mask has to sit above A; the next multiple of five is the owner's own
#: granularity for a floor (30, 40).
ALT_STEP_DEG = 5.0

#: Refusals above this altitude are not filed. A tree line does not reach 60
#: degrees from where a telescope stands; an obstruction there is a roof or a
#: closed dome, which a horizon mask models badly (it would deny the whole sky
#: above the mask).
MAX_SUGGEST_ALT_DEG = 60.0

#: Refusals a region needs between its bins. Four panels of a mosaic pass that
#: all failed are four frames; one hold pass is already enough, one frame is not.
MIN_REGION_FRAMES = 3

#: Nights kept. A tree does not move, but a night's frames mean little a month
#: on, and the cap keeps the store bounded.
MAX_NIGHTS = 14

#: The drawn horizon covers a bin when it is at least the mask minus this.
COVER_TOLERANCE_DEG = 0.5

#: A plateau stops this far inside its bin edge, and the line ramps from the
#: sky to a plateau over ``RAMP_DEG`` before it. The steep edge matches the
#: scanner's own (a 0.02 degree ramp between a wall and open sky).
EDGE_DEG = 0.01
RAMP_DEG = 1.0

#: What a line is rounded to, decimal places. Rounded UP, so rounding can never
#: lower the line.
_DECIMALS = 3

_NO_HORIZON = ("no horizon is drawn for this site; if something blocks this "
               "direction, draw it on the Site page")
_WAITING = ("a horizon suggestion from earlier obstructed frames is waiting on "
            "the Site page")


@dataclass
class _Bin:
    """What one azimuth bin knows: how many refusals and the highest, how many
    clear frames and the lowest."""
    refused_n: int = 0
    refused_max_alt: float = -math.inf
    clear_n: int = 0
    clear_min_alt: float = math.inf


@dataclass(frozen=True)
class Region:
    """A run of neighbouring bins the frames agree on. ``az_from`` is greater
    than ``az_to`` when the run crosses north."""
    az_from: float
    az_to: float
    alt_min: float
    alt_max: float
    frames: int


@dataclass(frozen=True)
class Suggestion:
    """The offer: the regions, and the whole line accept would write (the drawn
    horizon with the regions raised into it). ``id`` follows the line, so an
    accept names the one the operator was shown."""
    id: str
    regions: tuple[Region, ...]
    points: list[list[float]]
    frames: int
    bins: int
    nights: int

    def as_dict(self) -> dict:
        return {"id": self.id, "frames": self.frames, "bins": self.bins,
                "nights": self.nights, "points": self.points,
                "regions": [{"az_from": r.az_from, "az_to": r.az_to,
                             "alt_min": r.alt_min, "alt_max": r.alt_max,
                             "frames": r.frames} for r in self.regions]}


@dataclass(frozen=True)
class _Candidate:
    index: int
    alt: float
    frames: int


def _bin_of(az_deg: float) -> int:
    return int((az_deg % 360.0) // AZ_BIN_DEG)


def _bins_per_circle() -> int:
    return int(round(360.0 / AZ_BIN_DEG))


def _ceil_to(value: float, step: float) -> float:
    return math.ceil(value / step - 1e-9) * step


def _line_value(drawn, az: float) -> float:
    """The drawn horizon at ``az``; 0 when nothing is drawn."""
    from .sequence.schedule import interp_wrap     # lazy: schedule is heavy
    return interp_wrap(drawn, az) if drawn else 0.0


class HorizonLearner:
    """The per-night store of refusals and clear frames, by azimuth bin."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nights: dict[str, dict[int, _Bin]] = {}
        self._announced_bins = 0

    def clear(self) -> None:
        with self._lock:
            self._nights = {}
            self._announced_bins = 0

    def nights(self) -> int:
        with self._lock:
            return len(self._nights)

    def note(self, frame_class, alt_deg: float, az_deg: float,
             now: float) -> None:
        """File one solve frame. ``OBSTRUCTED`` is a refusal, ``STARS`` is a
        frame that centred; every other class says nothing about a horizon."""
        cls = FrameClass(frame_class)
        if cls not in (FrameClass.OBSTRUCTED, FrameClass.STARS):
            return
        if not (0.0 <= alt_deg <= MAX_SUGGEST_ALT_DEG):
            return
        with self._lock:
            night = self._nights.setdefault(night_key(now), {})
            while len(self._nights) > MAX_NIGHTS:
                del self._nights[min(self._nights)]
            cell = night.setdefault(_bin_of(az_deg), _Bin())
            if cls is FrameClass.OBSTRUCTED:
                cell.refused_n += 1
                cell.refused_max_alt = max(cell.refused_max_alt, alt_deg)
            else:
                cell.clear_n += 1
                cell.clear_min_alt = min(cell.clear_min_alt, alt_deg)
        if cls is FrameClass.OBSTRUCTED:
            self._announce()

    def _announce(self) -> None:
        """Say once, in counts, that a region has formed or grown by a bin."""
        found = self._regions(None)
        bins = sum(len(run) for run in found)
        with self._lock:
            if bins <= self._announced_bins:
                return
            self._announced_bins = bins
        frames = sum(c.frames for run in found for c in run)
        bus.log("info",
                f"horizon suggestion ready: {bins} {'bin' if bins == 1 else 'bins'}"
                f" from {frames} obstructed frames; review it on the Site page",
                "site")

    def _merged(self) -> dict[int, _Bin]:
        """Every retained night folded into one bin map. Call with the lock
        held."""
        merged: dict[int, _Bin] = {}
        for night in self._nights.values():
            for idx, cell in night.items():
                into = merged.setdefault(idx, _Bin())
                into.refused_n += cell.refused_n
                into.refused_max_alt = max(into.refused_max_alt,
                                           cell.refused_max_alt)
                into.clear_n += cell.clear_n
                into.clear_min_alt = min(into.clear_min_alt, cell.clear_min_alt)
        return merged

    def _candidates(self, drawn) -> list[_Candidate]:
        """The bins whose frames agree and the drawn horizon does not yet cover,
        each with the mask altitude it asks for."""
        with self._lock:
            merged = self._merged()
        out = []
        for idx in sorted(merged):
            cell = merged[idx]
            if cell.refused_n == 0 or cell.clear_min_alt <= cell.refused_max_alt:
                continue
            alt = min(_ceil_to(cell.refused_max_alt, ALT_STEP_DEG),
                      cell.clear_min_alt)
            lo = idx * AZ_BIN_DEG
            covered = min(_line_value(drawn, az)
                          for az in (lo + EDGE_DEG, lo + AZ_BIN_DEG / 2,
                                     lo + AZ_BIN_DEG - EDGE_DEG))
            if covered >= alt - COVER_TOLERANCE_DEG:
                continue
            out.append(_Candidate(idx, alt, cell.refused_n))
        return out

    def _regions(self, drawn) -> list[list[_Candidate]]:
        """Runs of neighbouring candidate bins (around north included) that
        hold at least ``MIN_REGION_FRAMES`` refusals between them."""
        cands = {c.index: c for c in self._candidates(drawn)}
        n = _bins_per_circle()
        if len(cands) == n:
            runs = [[cands[i] for i in range(n)]]
        else:
            start = next((i for i in cands if (i - 1) % n not in cands), None)
            runs = []
            seen: set[int] = set()
            while start is not None:
                run, i = [], start
                while i in cands and i not in seen:
                    seen.add(i)
                    run.append(cands[i])
                    i = (i + 1) % n
                runs.append(run)
                start = next((j for j in cands
                              if j not in seen and (j - 1) % n not in cands), None)
        return [run for run in runs
                if sum(c.frames for c in run) >= MIN_REGION_FRAMES]

    def suggestion(self, drawn) -> Suggestion | None:
        """The offer against ``drawn`` (the horizon now configured, ``[(az,
        alt), ...]`` or None), or None when no region has formed."""
        runs = self._regions(drawn)
        if not runs:
            return None
        points = _merged_line(drawn, runs)
        regions = tuple(_region_of(run) for run in runs)
        digest = hashlib.sha1(json.dumps(points).encode("ascii")).hexdigest()
        cands = [c for run in runs for c in run]
        return Suggestion(id=digest[:12], regions=regions, points=points,
                          frames=sum(c.frames for c in cands), bins=len(cands),
                          nights=self._nights_behind(cands))

    def _nights_behind(self, cands: list[_Candidate]) -> int:
        """How many retained nights have a refusal in any of ``cands``."""
        wanted = {c.index for c in cands}
        with self._lock:
            return sum(1 for night in self._nights.values()
                       if any(idx in wanted and cell.refused_n
                              for idx, cell in night.items()))


def _region_of(run: list[_Candidate]) -> Region:
    return Region(az_from=run[0].index * AZ_BIN_DEG,
                  az_to=(run[-1].index + 1) * AZ_BIN_DEG % 360.0 or 360.0,
                  alt_min=min(c.alt for c in run),
                  alt_max=max(c.alt for c in run),
                  frames=sum(c.frames for c in run))


def _plateaus(run: list[_Candidate]) -> list[tuple[float, float, float]]:
    """``(az_from, az_to, alt)`` for a run, neighbouring bins of one altitude
    joined into one plateau."""
    out: list[tuple[float, float, float]] = []
    for c in run:
        lo, hi = c.index * AZ_BIN_DEG, (c.index + 1) * AZ_BIN_DEG
        if out and out[-1][2] == c.alt:
            out[-1] = (out[-1][0], hi, c.alt)
        else:
            out.append((lo, hi, c.alt))
    return out


def _merged_line(drawn, runs: list[list[_Candidate]]) -> list[list[float]]:
    """The drawn horizon with every plateau raised into it, as the points of a
    line the horizon rule accepts.

    The nodes are the drawn line's own points plus, for each run, the two ends
    of every plateau (an edge inside its bin) and a foot a degree outside each
    end of the run. Each node takes the higher of the drawn line and the
    suggestion there. Both are piecewise linear and every node of either is a
    node here, so the chord between two nodes of the result is never below the
    drawn line: the merge cannot lower a floor."""
    nodes: dict[float, float] = {}

    def put(az: float, suggested: float) -> None:
        az = round(az % 360.0, _DECIMALS)
        value = max(_line_value(drawn, az), suggested)
        nodes[az] = max(nodes.get(az, -math.inf), value)

    for az, _alt in (drawn or ()):
        put(float(az), -math.inf)
    for run in runs:
        plateaus = _plateaus(run)
        put(plateaus[0][0] - RAMP_DEG, 0.0)
        for lo, hi, alt in plateaus:
            put(lo + EDGE_DEG, alt)
            put(hi - EDGE_DEG, alt)
        put(plateaus[-1][1] + RAMP_DEG, 0.0)
    line = [[az, math.ceil(nodes[az] * 10 ** _DECIMALS) / 10 ** _DECIMALS]
            for az in sorted(nodes)]
    from .locations import sanitize_horizon_points   # thins past the cap
    kept, _problems = sanitize_horizon_points(line)
    return [list(p) for p in kept]


#: The one learner the engine feeds and the routes read.
LEARNER = HorizonLearner()


def note_solve_frame(frame_class, ra_hours: float, dec_deg: float, *,
                     latitude: float, longitude: float, now: float,
                     learner: HorizonLearner | None = None) -> None:
    """File the frame of a centring solve under where the mount was pointing.

    The engine calls this where it classifies a failed solve (an obstruction)
    and where a centring succeeded (``FrameClass.STARS``). The pointing's
    altitude and azimuth are computed here and never leave this module except
    through the routes that serve a ``view.site_derived`` principal."""
    if FrameClass(frame_class) not in (FrameClass.OBSTRUCTED, FrameClass.STARS):
        return
    alt, az = altaz(ra_hours, dec_deg, latitude, longitude, now)
    (learner or LEARNER).note(frame_class, alt, az, now)


def hold_hint(drawn, learner: HorizonLearner | None = None) -> str | None:
    """What a centring hold may tell the operator about the horizon: that none
    is drawn and where to draw it, or that a suggestion is waiting. None when a
    horizon is drawn and nothing is waiting (#1093)."""
    waiting = (learner or LEARNER).suggestion(drawn) is not None
    if waiting:
        return _WAITING if drawn is not None else f"{_NO_HORIZON}; {_WAITING}"
    return None if drawn is not None else _NO_HORIZON
