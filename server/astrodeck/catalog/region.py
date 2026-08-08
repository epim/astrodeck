""""What catalogued objects are inside this sky region" -- written once for
both queued callers: a camera-frame overlay (what's in the field the rig is
pointed at) and the Atlas viewport (what's in the patch of sky currently
panned/zoomed into).

CENTRE + RADIUS, NOT A BOUNDING BOX. Both callers already think in exactly
that shape: a camera frame is "pointed at (ra, dec), FOV this wide" -- use
radius = half the frame's diagonal, a circle that circumscribes the (usually
rectangular) sensor, generous rather than exact; the Atlas viewport is "panned
to (ra, dec), showing this much sky" -- radius = half the visible diagonal at
the current zoom. Neither caller has to convert into RA/Dec min/max pairs,
and RA/Dec min/max pairs are exactly where a region test goes wrong:

  * AT THE RA WRAP (0h/24h): a box test ``ra_min <= ra <= ra_max`` silently
    excludes real hits when the box straddles the wrap -- a viewport centred
    at 23.9h showing +/-2h has ra_min=21.9, ra_max=25.9 (not a valid hour), or
    if naively wrapped to [21.9, 24) union [0, 1.9), a plain single-range
    test finds neither half.
  * AT THE POLE: near dec=+/-90, EVERY right ascension is "close" -- a box on
    RA is not just wrong there, it's meaningless (lines of RA converge to a
    point).

A centre+radius query sidesteps both, structurally, by never comparing RA or
Dec components directly: it calls ``coords.angular_sep_deg``, the existing
spherical-law-of-cosines great-circle distance (already used for the Sun/Moon
avoidance checks elsewhere in this package), and keeps only objects whose true
angular separation from the centre is within radius. Both failure modes above
are exercised explicitly in tests/test_catalog_region.py.

RANKING. A wide field can contain hundreds of catalogued objects -- the
Virgo Cluster core alone has dozens of anonymous PGC/ESO galaxies within a
single degree -- and showing all of them is not an annotated sky, it's
noise. ``_rank_score`` orders candidates so a caller can take the top N and
get the ones a human would actually recognise or notice, not just whatever
happened to fall inside the circle. See its docstring for the four signals
and why they're weighted the way they are. Label placement / de-collision
for whatever N the caller keeps is explicitly NOT this module's job.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable

from .coords import angular_sep_deg
from .objects import CATALOG, MAG_UNKNOWN, DSO

_CATALOGUE_PREFIX = re.compile(r"^[A-Za-z]+")

#: Catalogue prominence: how likely a beginner is to recognise the CATALOGUE
#: itself, independent of the object. Messier is the original "110 things
#: worth pointing at" list and what most charts/apps label first; NGC is the
#: next tier a casual chart shows; IC a notch below; everything else (ESO,
#: PGC, UGC, HCG, Cl, Sh2, B, C...) is a survey/deep-catalogue identifier
#: nobody recognises on sight. Deliberately a SMALL contribution (see
#: `_rank_score`) -- it breaks ties, it does not override brightness.
_PROMINENCE = {"M": 3.0, "NGC": 2.0, "IC": 1.0}


def _prominence(obj_id: str) -> float:
    m = _CATALOGUE_PREFIX.match(obj_id)
    return _PROMINENCE.get(m.group(0), 0.0) if m else 0.0


def _rank_score(obj: DSO) -> float:
    """Higher = more worth a label in a crowded frame.

    Four signals, weighted so brightness dominates and the rest break ties
    among objects of similar brightness:

      * MAGNITUDE, the dominant term (weight 2x the others). ``-obj.mag`` so
        numerically brighter (smaller, even negative) scores higher. An
        object with no published magnitude (``objects.MAG_UNKNOWN``) is
        scored as fainter than anything actually measured -- "unknown" must
        never outrank a real faint measurement just because the sentinel is
        a small float by accident.
      * ANGULAR SIZE, log-scaled. A single field can hold both a 3' galaxy
        and the 646'-wide LMC; without the log, size alone would let the LMC
        swamp every ranking regardless of brightness. Floored at 0.1' so a
        point-like/no-size entry doesn't send log10 to -inf.
      * A FLAT BONUS for having a real common name. A named object is one a
        user recognises on sight ("oh, the Ring Nebula") even when a fainter
        anonymous PGC galaxy in the same frame is technically brighter by a
        fraction of a magnitude -- but the bonus is bounded, so a named yet
        very faint object still cannot outrank an unnamed showpiece.
      * CATALOGUE PROMINENCE (`_prominence`), the smallest term: Messier over
        NGC over IC over a survey identifier, as a last tie-breaker only.
    """
    brightness = -obj.mag if obj.mag < MAG_UNKNOWN else -30.0
    size = math.log10(max(obj.size_arcmin, 0.1))
    named = 4.0 if obj.name != obj.id else 0.0
    return brightness * 2.0 + size + named + _prominence(obj.id)


def objects_in_region(ra_hours: float, dec_deg: float, radius_deg: float,
                      limit: int | None = None,
                      catalog: Iterable[DSO] | None = None) -> list[DSO]:
    """Catalog objects within ``radius_deg`` of (``ra_hours``, ``dec_deg``),
    most-worth-showing first, so a caller takes ``result[:N]`` for however
    many labels it has room for.

    ``catalog`` defaults to ``objects.CATALOG`` (all 13,370); a caller may
    pass a pre-filtered iterable (e.g. only objects with a magnitude) to rank
    over a subset instead. ``limit=None`` returns every hit, ranked -- a
    caller that wants to apply its own secondary filter (e.g. de-collision
    against label boxes already placed) before truncating needs the full
    ranked list, not just the top N by this module's opinion of "worth it".

    Ties (identical score -- realistically only two objects with no
    magnitude, no name, and the same size) break on id, so results are
    stable across calls rather than depending on Python's sort stability
    against whatever order ``catalog`` happened to iterate in.
    """
    src = CATALOG if catalog is None else catalog
    hits = [o for o in src
            if angular_sep_deg(ra_hours, dec_deg, o.ra_hours, o.dec_deg) <= radius_deg]
    hits.sort(key=lambda o: (-_rank_score(o), o.id))
    return hits[:limit] if limit is not None else hits
