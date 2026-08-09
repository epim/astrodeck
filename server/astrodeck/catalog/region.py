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

THREE SOURCES, ONE ANSWER (2026-08-08). ``objects_in_region`` is deep-sky
only, because ``CATALOG`` is. The Atlas has to mark what is actually in the
sky, which also means the 241 named stars in ``brightstars`` and the live
Sun/Moon/planets in ``solar_system`` -- the same three sources
``objects.search`` already merges for the text box. ``region_rows`` is the
position-shaped twin of that merge, and ``GET /api/catalog/region`` at the
bottom of this file is its one caller. Everything the Atlas draws and
everything its info card shows comes from that single response: there is no
per-object detail route, so tapping a marker in the dark costs no round trip
and works with the network down.
"""
from __future__ import annotations

import asyncio
import math
import re
import time as _time
from collections.abc import Iterable

from fastapi import APIRouter, Depends, Query

from ..auth import CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS, Principal, require
from ..auth.rbac import declare
from .constellations import constellation_for
from .coords import angular_sep_deg
from .describe import describe
from .objects import _ALIASES, _TYPE_NAMES, CATALOG, MAG_UNKNOWN, DSO

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


def _score(mag: float, size_arcmin: float, named: bool, prominence: float) -> float:
    """The ranking arithmetic, shared by every kind of thing this module can
    return. See ``_rank_score`` for what the four signals are and why they are
    weighted this way; this exists only so a named star and a planet -- neither
    of which is a ``DSO`` -- are ranked against deep-sky objects by the SAME
    expression rather than by a second one that can drift away from it."""
    brightness = -mag if mag < MAG_UNKNOWN else -30.0
    size = math.log10(max(size_arcmin, 0.1))
    return brightness * 2.0 + size + (4.0 if named else 0.0) + prominence


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
    return _score(obj.mag, obj.size_arcmin, obj.name != obj.id,
                  _prominence(obj.id))


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

    THE DECLINATION PREFILTER is an optimisation with a proof, NOT a second
    region test -- there is still exactly one notion of "inside", and it is
    still ``angular_sep_deg``. ``|dec_a - dec_b| <= sep`` holds for every pair
    of points on a sphere:

        cos(sep) = sin d_a sin d_b + cos d_a cos d_b cos(dRA)
                <= sin d_a sin d_b + cos d_a cos d_b            (cos(dRA) <= 1)
                 = cos(d_a - d_b)

    and cos is decreasing on [0, pi], so sep >= |d_a - d_b|. An object whose
    declination alone is further than ``radius_deg`` therefore CANNOT be
    inside the circle, and skipping it changes no result -- only the six
    trigonometric calls it would have cost. Note what this deliberately is
    NOT: a test on RIGHT ASCENSION, which is where the wrap and the pole
    failures in this module's header live. Declination has no wrap and does
    not converge anywhere. Measured over the 13,370-row catalogue: 5.1 ms ->
    0.8 ms for a 2 deg query and 6.2 ms -> 3.6 ms for a 20 deg one, which is
    what makes a per-viewport query affordable on a Pi while the Atlas is
    being panned.
    """
    src = CATALOG if catalog is None else catalog
    hits = [o for o in src
            if abs(o.dec_deg - dec_deg) <= radius_deg
            and angular_sep_deg(ra_hours, dec_deg, o.ra_hours, o.dec_deg) <= radius_deg]
    hits.sort(key=lambda o: (-_rank_score(o), o.id))
    return hits[:limit] if limit is not None else hits


# =============================================================================
# All three catalog sources, in one region
# =============================================================================

#: A deep-sky catalogue that failed to load leaves ``objects.CATALOG`` at the
#: 64 hand-written curated rows (``objects._load_bulk`` degrades rather than
#: raises). A region answered from 64 objects looks exactly like an empty patch
#: of sky, so the answer has to SAY so -- see ``catalog_degraded`` on the wire.
_CURATED_ONLY = 64


def catalog_degraded() -> bool:
    """The bulk deep-sky file did not load, so every region answer is being
    served from the curated core alone."""
    return len(CATALOG) <= _CURATED_ONLY


def offered_at_fov(fov_deg: float, obj: DSO) -> bool:
    """Is this object worth OFFERING at a view this wide?

    A 40 deg free-roam view geometrically contains thousands of catalogued
    objects and a 0.2 deg view contains a handful; the same list cannot serve
    both. This is a membership test by zoom band -- ONE table rather than a
    rule per object type -- and it is deliberately separate from
    ``_rank_score``, which orders whatever is offered.

    Membership by zoom, ORDER independent of zoom, is the split that matters:
    a score that moved with the zoom would re-order labels continuously
    through a pinch, and the client's label placement keeps a label's slot and
    anchor across frames precisely to stop that flicker. Band boundaries are
    crossed rarely and discretely; a continuous re-rank is crossed constantly.

    An object with no published magnitude (1,823 of them) passes a band only
    through a clause that is not about brightness -- its size, its name, or
    the catch-all narrow band. "Unmeasured" is never read as "bright enough",
    and never as "too faint to mention" either.

    EACH BAND IS A SUPERSET OF THE ONE ABOVE IT, and that is structural here
    rather than a property the four rules happen to have: every band is
    written as the previous band's clause OR one more. Without it the table
    is not monotone, and a non-monotone table makes labels DISAPPEAR AS YOU
    ZOOM IN. The first draft of this function had exactly that -- a named
    galaxy at mag 15 (NGC 3172, "Polarissima Borealis") was offered at 40 deg,
    dropped at 10 deg, offered again at 2 deg and dropped again at 0.2 deg,
    because the four rules were independent thresholds rather than a
    widening. ``test_zoom_bands_are_monotone`` is the guard, and it walks the
    whole catalogue rather than a chosen example.
    """
    named = obj.name != obj.id
    messier = _prominence(obj.id) >= _PROMINENCE["M"]
    mag_known = obj.mag < MAG_UNKNOWN
    # Whole-constellation view: only what a paper chart would print.
    chart = messier or named or (mag_known and obj.mag < 8.0)
    if fov_deg > 20.0:
        return chart
    if fov_deg > 5.0:
        return chart or (mag_known and obj.mag < 11.0) or obj.size_arcmin > 10.0
    if fov_deg > 0.5:
        return (chart or (mag_known and obj.mag < 11.0)
                or obj.size_arcmin > 10.0 or (mag_known and obj.mag < 13.0))
    # Below half a degree the view IS one object's neighbourhood, and the
    # question stops being "what is worth showing" and becomes "what is in
    # this frame". Everything is. How many of them get TEXT is the client's
    # label budget, which is a different question with a different answer;
    # thinning membership here would also delete the marker and the tap
    # target, not just the label.
    return True


def _dso_region_row(obj: DSO, sep_deg: float) -> dict:
    """One deep-sky object, in the shape the Atlas draws and its card reads."""
    return {
        "id": obj.id,
        # What gets drawn next to the marker. Falls back to the designation,
        # which ``objects._load_bulk`` already stored as the name for the
        # 13,197 rows OpenNGC publishes no common name for.
        "label": obj.name,
        "kind": "dso",
        "type": _TYPE_NAMES[obj.type],
        "ra_hours": obj.ra_hours,
        "dec_deg": obj.dec_deg,
        # None, never 99: MAG_UNKNOWN is a sentinel for "nobody published one",
        # and a card that prints it as a brightness is inventing a measurement.
        "mag": None if obj.mag >= MAG_UNKNOWN else obj.mag,
        "size_arcmin": obj.size_arcmin,
        "constellation": constellation_for(obj.id),
        "describe": describe(obj),
        # 109 rows carry one ("NGC 224" for M31). The single most useful line
        # on the card for anyone holding a printed chart.
        "alias": _ALIASES.get(obj.id),
        "sep_deg": round(sep_deg, 4),
    }


def _star_region_rows(ra_hours: float, dec_deg: float,
                      radius_deg: float) -> list[tuple[float, dict]]:
    """(score, row) for every named star inside the circle.

    241 rows, all of them naked-eye bright, so there is no zoom band to apply:
    a star that is in the field is worth marking at any zoom.
    """
    from . import brightstars

    out: list[tuple[float, dict]] = []
    for star in brightstars.STARS:
        if abs(star.dec_deg - dec_deg) > radius_deg:      # see the proof above
            continue
        sep = angular_sep_deg(ra_hours, dec_deg, star.ra_hours, star.dec_deg)
        if sep > radius_deg:
            continue
        out.append((
            _score(star.mag, 0.0, True, 0.0),
            {
                "id": star.name,
                "label": star.name,
                "kind": "star",
                "type": brightstars.TYPE_NAME,
                "ra_hours": star.ra_hours,
                "dec_deg": star.dec_deg,
                "mag": star.mag,
                # A star is a point source at any focal length this rig will
                # ever have; 0 is what difficulty.surface_brightness_mag
                # already reads as "no disc".
                "size_arcmin": 0.0,
                "constellation": brightstars.CONSTELLATIONS[star.con][0],
                "describe": brightstars.describe(star),
                "alias": brightstars.designation(star) or None,
                "sep_deg": round(sep, 4),
            },
        ))
    return out


# --------------------------------------------------------------- the ephemeris
#: How long a computed set of Sun/Moon/planet positions may be reused.
#:
#: Measured on this tree: ``solar_system.position`` costs ~6 ms per body and
#: ~58 ms for the eight offered bodies (plus ~500 ms once, for astropy's own
#: import and IERS init) -- and the Atlas asks for a region every time the view
#: leaves its cached patch. Paying 58 ms of astropy per pan on a Pi is not a
#: trade worth making for a marker.
#:
#: 120 s is chosen against the fastest thing in the list: the Moon moves ~0.55
#: deg/hour, so a two-minute-old position is at most ~1.1' stale -- under the
#: Moon's own apparent radius, and under one marker width at every Atlas zoom
#: wider than about 0.2 deg. Every solar-system row carries the unix time it
#: was computed for, so the staleness is stated on the wire rather than hidden.
_EPHEM_TTL_S = 120.0

#: (cache key, computed_at_unix, rows). Module-level: the positions are a
#: function of TIME and SITE only, never of the caller or the region.
_ephem_cache: tuple[tuple, float, list[dict]] | None = None


def _ephem_key() -> tuple:
    """Everything a cached set of body positions depends on besides the clock.

    The site matters because ``solar_system._observer`` uses it, and for the
    MOON that is not cosmetic -- horizontal parallax reaches ~1 deg. A site
    edit must not be served a stale set for two minutes.
    """
    from ..config import config_store

    site = config_store.cfg().site
    return (round(site.latitude, 6), round(site.longitude, 6),
            round(site.elevation_m, 3), bool(getattr(site, "is_default", False)))


def solar_system_rows(when: float | None = None) -> tuple[list[dict], list[str]]:
    """Every Sun/Moon/planet row we can compute, and the reasons for any we
    cannot. Cached for ``_EPHEM_TTL_S`` when ``when`` is None (i.e. "now");
    an explicit time always computes, so a test is never answered from a
    cache built for a different instant.

    THE SUN IS INCLUDED HERE, unlike ``solar_system.offered_bodies()``, and
    the difference is deliberate. That gate exists so a SEARCH does not offer a
    target the mount will refuse to slew to. A sky map is not an offer: a map
    that silently omits the Sun is lying about the sky, and where the Sun is
    happens to be the single most safety-relevant thing on it. The refusal is
    still carried -- as the row's own note, in the same words the mount would
    use -- rather than as a hole in the drawing.
    """
    global _ephem_cache
    from . import solar_system as ss

    live = when is None
    key = _ephem_key() if live else None
    if live and _ephem_cache is not None:
        cached_key, at, rows = _ephem_cache
        if cached_key == key and (_time.time() - at) < _EPHEM_TTL_S:
            return list(rows), []

    rows: list[dict] = []
    notes: list[str] = []
    for body in ss.BODIES:
        try:
            r = ss.row(body.key, when)
        except ss.EphemerisUnavailable as e:
            notes.append(
                f"{body.label} is not marked on the map: it could not be placed "
                f"just now ({e}). It is left out rather than drawn at a guessed "
                f"position.")
            continue
        rows.append({
            "id": r["id"],
            "label": r["id"],
            "kind": "solar_system",
            "type": r["type"],
            "ra_hours": r["ra_hours"],
            "dec_deg": r["dec_deg"],
            "mag": r["mag"],
            "size_arcmin": r["size_arcmin"],
            "constellation": r["constellation"],
            # solar_system.row puts its computed sentence in "name" -- the same
            # describe() every other kind here carries.
            "describe": r["name"],
            "alias": None,
            # WHEN this position was true, and whether we knew where the
            # observer was standing. Both are what make the row falsifiable.
            "ephemeris_unix": r["ephemeris_unix"],
            "topocentric": r["topocentric"],
        })
    if live and not notes:
        # Only a complete set is cached: a partial one would keep re-serving
        # the gap for two minutes after the transient that caused it cleared.
        _ephem_cache = (key, _time.time(), list(rows))
    return rows, notes


def _reset_ephemeris_cache() -> None:
    """Drop the cached body positions. For tests, and for anything that changes
    the site under a running process."""
    global _ephem_cache
    _ephem_cache = None


#: The Moon's published RA/Dec is a strong function of WHERE THE OBSERVER IS
#: STANDING -- lunar horizontal parallax reaches about 1 degree, which is a
#: hundred times the marker it draws. A pannable map that answers on every
#: viewport change is a far higher-rate oracle than a search box, and this repo
#: has already had a viewer geolocate the rig to 2.9 km through exactly this
#: shape. So the Moon rides the same capability alt/az does. The Sun and the
#: planets do not: their topocentric shift is under an arcsecond, which carries
#: no recoverable position at any precision this API publishes.
_SITE_DERIVED_BODIES = {"Moon"}

_MOON_WITHHELD_NOTE = (
    "The Moon is not marked: where it appears in the sky depends on where you "
    "are standing (up to about 1 degree), so its position would give away this "
    "rig's location. Everything else on this map is the same from anywhere on "
    "Earth.")


def region_rows(ra_hours: float, dec_deg: float, radius_deg: float, *,
                fov_deg: float | None = None,
                limit: int = 80,
                kinds: tuple[str, ...] = ("dso", "star", "solar_system"),
                site_derived: bool = True,
                when: float | None = None) -> tuple[list[dict], list[str], bool]:
    """Everything catalogued inside the circle, ranked, as wire rows.

    Returns ``(rows, notes, truncated)``. ``fov_deg`` is the width of the view
    the caller is drawing (defaults to the circle's diameter) and selects the
    zoom band -- see ``offered_at_fov``.

    The scores that produced the order are dropped here, because the Atlas draws
    from the ORDER and has no use for the numbers. ``scored_region_rows`` below
    is the same query with them kept; see its docstring for why identification
    needs them and must not re-derive them.
    """
    rows, notes, truncated = scored_region_rows(
        ra_hours, dec_deg, radius_deg, fov_deg=fov_deg, limit=limit,
        kinds=kinds, site_derived=site_derived, when=when)
    return [row for _, row in rows], notes, truncated


def scored_region_rows(ra_hours: float, dec_deg: float, radius_deg: float, *,
                       fov_deg: float | None = None,
                       limit: int = 80,
                       kinds: tuple[str, ...] = ("dso", "star", "solar_system"),
                       site_derived: bool = True,
                       when: float | None = None,
                       ) -> tuple[list[tuple[float, dict]], list[str], bool]:
    """``region_rows`` with each row's rank score kept beside it.

    ``identify_field`` needs the scores, and the ONE thing it must not do is
    recompute them from the finished rows: a wire row has already dropped
    ``MAG_UNKNOWN`` to ``None`` and flattened three different kinds of object
    into one shape, so a second expression reading it back would rank the same
    object differently from the expression that ordered it -- two rankings of one
    object inside one feature, which the header of ``region_rows`` was already
    written to prevent. So the score is computed once, by ``_score``, and carried.

    Solar-system rows survive truncation. There are at most nine of them, they
    are what a beginner asks about first, and they are the only rows on this
    map that MOVE -- an answer that dropped Jupiter because a dense patch of
    Sagittarius filled the budget would be dropping the one thing the user
    could not have found on a printed chart.
    """
    fov = fov_deg if fov_deg is not None else radius_deg * 2.0
    # (score, row) throughout: the score is COMPUTED ONCE per object, by the
    # one expression that knows how to compute it, and then carried. Nothing
    # below re-derives a score from a finished row -- that is how two rankings
    # of the same object end up in one function and disagree.
    scored: list[tuple[float, dict]] = []
    fixed: list[tuple[float, dict]] = []
    notes: list[str] = []

    if "dso" in kinds:
        for obj in objects_in_region(ra_hours, dec_deg, radius_deg):
            if not offered_at_fov(fov, obj):
                continue
            sep = angular_sep_deg(ra_hours, dec_deg, obj.ra_hours, obj.dec_deg)
            scored.append((_rank_score(obj), _dso_region_row(obj, sep)))

    if "star" in kinds:
        scored.extend(_star_region_rows(ra_hours, dec_deg, radius_deg))

    if "solar_system" in kinds:
        bodies, body_notes = solar_system_rows(when)
        notes.extend(body_notes)
        withheld = False
        for row in bodies:
            if row["id"] in _SITE_DERIVED_BODIES and not site_derived:
                withheld = True
                continue
            sep = angular_sep_deg(ra_hours, dec_deg,
                                  row["ra_hours"], row["dec_deg"])
            if sep > radius_deg:
                continue
            # Prominence 4.0: a planet is not in any catalogue this ranking
            # knows, and it is the first thing anyone points a new telescope at.
            fixed.append((_score(row["mag"], row["size_arcmin"], True, 4.0),
                          dict(row, sep_deg=round(sep, 4))))
        if withheld:
            notes.append(_MOON_WITHHELD_NOTE)

    room = max(0, limit - len(fixed))
    scored.sort(key=lambda t: (-t[0], t[1]["id"]))
    truncated = len(scored) > room
    merged = fixed + scored[:room]
    merged.sort(key=lambda t: (-t[0], t[1]["id"]))
    return merged, notes, truncated


# =============================================================================
# A SOLVED CAMERA FRAME: which objects are in it, and which one NAMES it
# =============================================================================
#
# The rectangle sibling of ``objects_in_region``, for #182. Everything here
# takes a ``WcsSolution`` -- a PLATE SOLVE -- and nothing here takes a mount's
# reported position, because on this rig those are not interchangeable: the AM5
# has no brake and has been found 50 degrees from where it claimed. A caller
# that has only reported pointing can still ask ``objects_in_region`` for a
# guess, and hub.py labels that answer ``source="pointing"`` all the way to the
# pixel that renders it; nothing derived that way is ever written to a file.
#
# WHAT THIS MODULE DELIBERATELY DOES NOT DO: it never returns a name for the
# PLAN. ``sequence.models.Target.name`` is chosen before the mount moves, keys
# the persisted per-target frame counter (``hub._capture_path``), gates
# ``only_target`` instructions and groups the report -- and an identification
# that drifts onto a neighbour between frame 3 and frame 4 would split one
# night across two folders with two overlapping ``0001...`` runs and no error
# anywhere. Everything below is INFORMATIONAL: a proposal for a human, a
# provenance card in a header, a marker on a picture.

#: FITS pixel coordinates are 1-based and refer to pixel CENTRES; numpy array
#: indices are 0-based. CRPIX is FITS, ``frame.data`` is numpy, and forgetting
#: the offset puts every marker one pixel out -- invisible on a 4000px frame and
#: therefore exactly the kind of error that survives review.
_FITS_PIXEL_ORIGIN = 1.0

#: How much a candidate is penalised for sitting off the field centre, in the
#: units ``_score`` already speaks: it weights magnitude at 2.0 per mag, so 6.0
#: means "an object at the very edge of the frame must be three magnitudes more
#: conspicuous than one at the centre to name the field instead".
#:
#: Expressed as a SUBTRACTION rather than the multiplication a first draft used,
#: because ``_score`` is not a 0..1 quantity -- it runs from about -61 (an
#: unmeasured, unnamed, tiny object) to about +14 (Sirius), and multiplying a
#: negative score by a positive centrality factor REVERSES the ordering exactly
#: where it matters, making the least conspicuous object in a faint field win
#: for being nearest the middle.
_OFF_CENTRE_PENALTY = 6.0

#: The margin by which the winner must beat the runner-up to be ``confident``,
#: in the same units: 4.0 == two magnitudes. ``confident`` is the gate on
#: everything that WRITES (hub.py only adopts an identification into the FITS
#: ``OBJECT`` card when it is confident, solve-derived, and the operator left
#: the field empty), so it is deliberately a wide margin rather than a tiebreak.
_CONFIDENT_MARGIN = 4.0


class WcsPlate:
    """A linear TAN plate solution, in both directions, over plain floats.

    Wraps ``solve.base.WcsSolution`` rather than astropy: the solution is a
    linear plate (CD matrix or CDELT/CROTA2 -- ``WcsSolution`` allows either and
    ``fitsio._apply_wcs`` writes either), so world<->pixel is a 2x2 inverse
    around the shared gnomonic in ``catalog.framing`` and needs neither the
    astropy WCS machinery nor a FITS header round-trip. That matters because
    this runs per preview frame on a Pi, and because the same two functions then
    serve the marker overlay and the identification with no second convention
    between them.

    SIP DISTORTION IS NOT MODELLED. ``WcsSolution`` carries none, so there is
    nothing to drop; at the fields this rig images the linear term is the whole
    story to well under a pixel. Recorded here rather than discovered later.
    """

    def __init__(self, wcs) -> None:
        cd = _cd_matrix(wcs)
        if cd is None:
            raise ValueError(
                "this WCS carries no scale (no CD matrix and no CDELT): it "
                "cannot place a pixel. A scale-less solution reads back as a "
                "silent 1 deg/px plate, which is worse than no solution at all")
        self.cd11, self.cd12, self.cd21, self.cd22 = cd
        det = self.cd11 * self.cd22 - self.cd12 * self.cd21
        if abs(det) < 1e-18:
            raise ValueError(
                "this WCS's CD matrix is singular (determinant 0): the two "
                "pixel axes map onto the same direction on the sky, so no "
                "position on it is recoverable")
        self._det = det
        self.crpix1 = float(wcs.crpix1)
        self.crpix2 = float(wcs.crpix2)
        self.ra0_hours = float(wcs.crval1) / 15.0
        self.dec0_deg = float(wcs.crval2)

    @property
    def pixel_scale_deg(self) -> float:
        """Degrees per pixel, as the geometric mean of the two axes. The square
        root of |det CD| is exactly that, and it is rotation-invariant -- so a
        rotated camera does not change the size a 10-arcminute galaxy is drawn."""
        return math.sqrt(abs(self._det))

    def to_pixel(self, ra_hours: float, dec_deg: float) -> tuple[float, float]:
        """Sky -> ``frame.data`` pixel (column, row), 0-based, float.

        Off-frame and behind-the-tangent-point positions come back as large
        finite numbers (see ``framing.H_EPS``) rather than raising, so a caller
        filters by the frame rectangle instead of by exceptions.
        """
        from .framing import project

        xi, eta = project(ra_hours, dec_deg, self.ra0_hours, self.dec0_deg)
        # invert [[cd11 cd12],[cd21 cd22]] @ (dx, dy) = (xi, eta)
        dx = (self.cd22 * xi - self.cd12 * eta) / self._det
        dy = (-self.cd21 * xi + self.cd11 * eta) / self._det
        return (self.crpix1 + dx - _FITS_PIXEL_ORIGIN,
                self.crpix2 + dy - _FITS_PIXEL_ORIGIN)

    def to_sky(self, x: float, y: float) -> tuple[float, float]:
        """``frame.data`` pixel (column, row), 0-based -> ``(ra_hours, dec_deg)``."""
        from .framing import deproject

        dx = x + _FITS_PIXEL_ORIGIN - self.crpix1
        dy = y + _FITS_PIXEL_ORIGIN - self.crpix2
        xi = self.cd11 * dx + self.cd12 * dy
        eta = self.cd21 * dx + self.cd22 * dy
        return deproject(xi, eta, self.ra0_hours, self.dec0_deg)


def _cd_matrix(wcs) -> tuple[float, float, float, float] | None:
    """``(cd11, cd12, cd21, cd22)`` for a solution that states either form, else
    ``None``.

    The CDELT/CROTA2 fallback is the classic AIPS rotation, and it is not
    hypothetical: ``WcsSolution`` declares both forms and ``fitsio._apply_wcs``
    writes whichever one the solver produced, so a plate that never carried a CD
    matrix must still place its objects rather than silently drawing nothing.
    """
    if wcs is None:
        return None
    if getattr(wcs, "cd11", None) is not None:
        return (float(wcs.cd11),
                float(wcs.cd12 or 0.0),
                float(wcs.cd21 or 0.0),
                float(wcs.cd22 if wcs.cd22 is not None else wcs.cd11))
    cdelt1 = getattr(wcs, "cdelt1", None)
    if cdelt1 is None:
        return None
    cdelt1 = float(cdelt1)
    cdelt2 = float(wcs.cdelt2) if getattr(wcs, "cdelt2", None) is not None else cdelt1
    rot = math.radians(float(getattr(wcs, "crota2", None) or 0.0))
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    return (cdelt1 * cos_r, -cdelt2 * sin_r, cdelt1 * sin_r, cdelt2 * cos_r)


def frame_geometry(wcs, width_px: int, height_px: int) -> dict:
    """Where a solved frame is and how big it is: centre, both field widths, and
    the radius of the circle that CIRCUMSCRIBES it.

    The circumscribing circle is what the cone query needs, and it is measured
    from the frame's actual corners rather than assumed from the pixel scale, so
    a rotated or slightly anisotropic plate is still fully covered. Generous
    rather than exact is the correct error here: a cone that is too small drops
    real objects out of a corner and nothing anywhere reports it.
    """
    plate = WcsPlate(wcs)
    w = max(1, int(width_px))
    h = max(1, int(height_px))
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    ra_c, dec_c = plate.to_sky(cx, cy)
    corners = [plate.to_sky(0.0, 0.0), plate.to_sky(w - 1.0, 0.0),
               plate.to_sky(0.0, h - 1.0), plate.to_sky(w - 1.0, h - 1.0)]
    radius = max(angular_sep_deg(ra_c, dec_c, ra, dec) for ra, dec in corners)
    scale = plate.pixel_scale_deg
    return {
        "ra_hours": ra_c,
        "dec_deg": dec_c,
        "fov_w_deg": w * scale,
        "fov_h_deg": h * scale,
        "radius_deg": radius,
        "pixel_scale_deg": scale,
        "plate": plate,
    }


def objects_in_frame(wcs, width_px: int, height_px: int, *,
                     limit: int = 25,
                     margin_frac: float = 0.10,
                     kinds: tuple[str, ...] = ("dso", "star", "solar_system"),
                     site_derived: bool = False,
                     when: float | None = None) -> dict:
    """What is in THIS frame, placed on THIS frame's pixels.

    Returns ``{"center", "fov_deg", "objects", "identification", "notes",
    "catalog_degraded"}``. ``objects`` rows are ``region_rows`` rows plus ``x``,
    ``y`` (``frame.data`` pixel space, 0-based), ``size_px`` and ``inside``.

    ``margin_frac`` keeps objects whose CENTRE is just outside the frame: a
    galaxy half in the corner is a fact about the picture, and dropping it makes
    the overlay disagree with what the user can plainly see. ``inside`` says
    which is which so the renderer can decide, and only an ``inside`` object may
    name the field.

    ``site_derived=False`` by DEFAULT here, unlike ``region_rows``. This answer
    rides the ``preview`` websocket event, which is broadcast to every connected
    client at whatever capability each holds; the Moon's apparent position is a
    function of where the observer is standing to about a degree, so publishing
    it beside a WCS would hand a viewer the same location oracle
    ``/api/catalog/region`` withholds. The withholding note comes back in
    ``notes`` rather than as a silent hole -- but ONLY when there was something
    to withhold. ``region_rows`` raises its note as soon as a site-derived body
    exists at all, which is correct for a whole-sky map and wrong for a
    half-degree frame: it would put "the Moon is not marked" under every single
    exposure and imply the Moon was in it. So the withholding happens HERE,
    after placement, using the same constants.
    """
    geom = frame_geometry(wcs, width_px, height_px)
    plate = geom["plate"]
    w = max(1, int(width_px))
    h = max(1, int(height_px))
    # The zoom band is chosen by the frame's WIDER side: an imaging frame is the
    # neighbourhood of one object, and asking the narrow side would offer a
    # thinner list than the picture actually shows.
    fov_deg = max(geom["fov_w_deg"], geom["fov_h_deg"])
    scored, notes, _truncated = scored_region_rows(
        geom["ra_hours"], geom["dec_deg"], geom["radius_deg"],
        fov_deg=fov_deg, limit=max(limit * 3, 60), kinds=kinds,
        # TRUE, then withheld below: the server is entitled to the position, the
        # broadcast is not. Asking for the rows and dropping them at the boundary
        # is what makes the note truthful about THIS frame.
        site_derived=True, when=when)
    notes = [n for n in notes if n != _MOON_WITHHELD_NOTE]

    mx, my = w * margin_frac, h * margin_frac
    placed: list[tuple[float, dict]] = []
    withheld = False
    for score, row in scored:
        x, y = plate.to_pixel(row["ra_hours"], row["dec_deg"])
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if x < -mx or x > w - 1 + mx or y < -my or y > h - 1 + my:
            continue
        if row["id"] in _SITE_DERIVED_BODIES and not site_derived:
            withheld = True
            continue
        size_arcmin = float(row.get("size_arcmin") or 0.0)
        placed.append((score, dict(
            row,
            x=round(x, 2), y=round(y, 2),
            size_px=round((size_arcmin / 60.0) / geom["pixel_scale_deg"], 1),
            inside=bool(0.0 <= x <= w - 1 and 0.0 <= y <= h - 1),
        )))

    if withheld:
        notes.append(_MOON_WITHHELD_NOTE)
    ident = identify_field(placed, fov_deg)
    return {
        "center": {"ra_hours": round(geom["ra_hours"], 6),
                   "dec_deg": round(geom["dec_deg"], 6)},
        "fov_w_deg": round(geom["fov_w_deg"], 5),
        "fov_h_deg": round(geom["fov_h_deg"], 5),
        "objects": [row for _, row in placed[:limit]],
        "identification": ident,
        "notes": notes,
        "catalog_degraded": catalog_degraded(),
    }


def identify_field(scored_rows, fov_deg: float) -> dict | None:
    """THE object that names this field, or ``None``.

    A different question from "what is worth a marker", and it turns on two
    things markers do not care about:

      * ITS CENTRE MUST BE IN THE FRAME. A showpiece just off the corner is
        worth drawing and is not what you are pointed at. When the rows carry
        ``inside`` (they do when they came through ``objects_in_frame``) that
        flag is the test, because it is the frame's real rectangle; a plain
        cone-derived list falls back to "within half the field of the centre",
        which is the same idea with a circle instead of a rectangle.
      * OFF-CENTRE COSTS. See ``_OFF_CENTRE_PENALTY``.

    ``confident`` -- the gate on everything that writes -- is true only when the
    winner beats the runner-up by ``_CONFIDENT_MARGIN``, or there is no runner-up
    at all. Two comparable objects in one frame is the normal state of a rich
    field, and the honest answer there is "here are two", not a coin flip
    stamped into a FITS header that every stacker downstream will believe.

    Returns ``None`` when nothing qualifies -- which is a COMMON and CORRECT
    answer (mosaic panels, calibration fields, most of the sky), not a failure.
    """
    if fov_deg <= 0:
        return None
    half = 0.5 * fov_deg
    ranked: list[tuple[float, dict]] = []
    for score, row in scored_rows:
        sep = float(row.get("sep_deg") or 0.0)
        if "inside" in row:
            if not row["inside"]:
                continue
        elif sep > half:
            continue
        off = sep / half if half > 0 else 0.0
        ranked.append((score - _OFF_CENTRE_PENALTY * off, row))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (-t[0], t[1]["id"]))
    best_score, best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    confident = runner is None or (best_score - runner[0]) >= _CONFIDENT_MARGIN
    return {
        "id": best["id"],
        "label": best["label"],
        "kind": best["kind"],
        "type": best["type"],
        "describe": best["describe"],
        "sep_arcmin": round(float(best.get("sep_deg") or 0.0) * 60.0, 2),
        "confident": confident,
        # Named, not just counted: "M 27, and it is not clearly M 27 rather than
        # NGC 6853" is a sentence a human can act on; "not confident" is not.
        "runner_up": None if runner is None else runner[1]["id"],
    }


# =============================================================================
# The route
# =============================================================================
router = APIRouter()


@router.get("/api/catalog/region")
@declare(CAP_VIEW_STATUS)
async def catalog_region(
    ra_hours: float = Query(..., ge=0.0, lt=24.0),
    dec_deg: float = Query(..., ge=-90.0, le=90.0),
    radius_deg: float = Query(..., gt=0.0, le=90.0),
    fov_deg: float | None = Query(None, gt=0.0, le=180.0),
    limit: int = Query(80, ge=1, le=400),
    principal: Principal = Depends(require(CAP_VIEW_STATUS)),
) -> dict:
    """What is in this patch of sky -- the Atlas's viewport query.

    NO ALTITUDE, NO AZIMUTH, EVER. ``/api/catalog`` adds them only for a holder
    of ``view.site_derived`` because every such pair is f(site, target) and the
    caller chooses the target, which makes a search box a coordinate oracle. A
    PANNABLE MAP is the same oracle at a far higher sample rate, and it does not
    need alt/az to draw a single marker -- so this route does not compute them
    at all rather than gating a field it would otherwise emit. The guards are
    ``test_no_row_of_any_kind_carries_an_altitude_or_azimuth`` and
    ``test_route_publishes_no_alt_or_az``, and they check every row of every
    kind rather than a sampled one.
    """
    # OFF the event loop, exactly as /api/catalog does and for the same reason:
    # the first solar-system query in a process pays astropy's import and IERS
    # init (~500 ms measured), and this route is hit while the user is panning.
    # Blocking the loop there stalls the 2 s status poll and the relay behind it.
    rows, notes, truncated = await asyncio.to_thread(
        region_rows, ra_hours, dec_deg, radius_deg,
        fov_deg=fov_deg, limit=limit,
        site_derived=principal.has(CAP_VIEW_SITE_DERIVED),
    )
    return {
        "center": {"ra_hours": ra_hours, "dec_deg": dec_deg},
        "radius_deg": radius_deg,
        "fov_deg": fov_deg if fov_deg is not None else radius_deg * 2.0,
        "rows": rows,
        "truncated": truncated,
        # A degraded catalogue answers "nothing here" for most of the sky and
        # looks exactly like a quiet patch. Stated, never inferred.
        "catalog_degraded": catalog_degraded(),
        "notes": notes,
    }
