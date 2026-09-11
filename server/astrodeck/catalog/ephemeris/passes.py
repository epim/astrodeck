"""Tonight's visible satellite passes.

THREE CONDITIONS, AND ALL THREE ARE REAL. A satellite being "up" is not the
same as a satellite being visible, and each of the extra two has cost somebody
a night standing in a field:

1. IT HAS TO CLEAR THIS SITE'S HORIZON -- the drawn one. The rig's horizon is a
   polyline of ``(azimuth, altitude)`` control points describing the actual tree
   line, and it is interpolated with ``sequence.schedule.interp_wrap``, which is
   the SAME function the sequence engine's obstruction rule uses. Not a
   reimplementation of it: if the engine would refuse to slew there, this must
   not promise a pass there, and two functions that agree today drift apart by
   the end of the year. Where no polyline is drawn, ``site.horizon_min_deg`` is
   the floor.

2. THE SATELLITE HAS TO BE IN SUNLIGHT. A satellite is a mirror; in the Earth's
   shadow there is nothing to see. Modelled with the CONICAL umbra and
   penumbra rather than a cylinder -- see ``satellites.shadow_state`` for why
   that is worth close to a minute at each end of an ISS pass.

3. THE OBSERVER HAS TO BE IN THE DARK. ``PASS_SUN_ALT_MAX_DEG`` is -6 degrees,
   civil twilight, and the choice is deliberate in both directions:

   * NOT -18 (astronomical twilight, the threshold the imaging side cares
     about). The best satellite passes happen in twilight, because that is when
     the observer is in shadow and the satellite overhead is still in sunlight.
     Waiting for astronomical dark would hide most of the passes worth watching.
   * NOT ``coords.dark_window``, even though that is this app's usual answer to
     "is it dark". ``dark_window`` returns the night's imaging window from
     ``safety.twilight_deg`` -- an OPERATOR PREFERENCE about when to open the
     shutter, defaulting to -12. Deriving pass visibility from it would mean a
     rig configured for a conservative -18 imaging start stopped reporting
     passes that anyone standing outside could plainly see. What the eye needs
     is a physical fact about the sky, so it gets its own constant.

COST. The search is 10-second steps over up to 72 hours, per satellite, which
is why every stage is vectorised: one SGP4 array call, one TEME -> ITRS array
transform (astropy caches the rotation matrices on the shared ``Time``, so only
the first satellite pays for them), and numpy for the rest. Entries and exits
are then refined to one second by bisection against the EXACT horizon function.
"""
from __future__ import annotations

import math
import time as _time

import numpy as np

from . import elements as _elements
from . import satellites as _sat

#: The observer's Sun altitude a pass has to happen under. See the module
#: docstring for why -6 and why not ``coords.dark_window``.
PASS_SUN_ALT_MAX_DEG = -6.0

#: Coarse search step. At ISS speed a 10-second step moves the satellite about
#: 4 degrees of arc, which cannot straddle a whole pass (the shortest useful one
#: is minutes long) and is cheap enough to run over the whole catalogue.
STEP_S = 10.0
#: What the entry and exit times are refined to.
REFINE_S = 1.0

DEFAULT_HOURS = 24.0
MAX_HOURS = 72.0

#: Resolution of the horizon lookup table built for the coarse scan. The table
#: exists because ``interp_wrap`` sorts its control points on every call, and
#: the coarse scan asks for a floor 8,640 times per satellite. The ANSWER a
#: caller sees never comes from the table: every reported entry and exit is
#: bisected against ``interp_wrap`` itself.
_FLOOR_TABLE_STEP_DEG = 0.1


def _cfg():
    from ...config import config_store

    return config_store.cfg()


def horizon_profile() -> tuple[list | None, float, str]:
    """``(polyline, floor_deg, source)`` for the configured site.

    ``source`` is one of ``site_polyline`` / ``horizon_min_deg`` / ``none`` and
    goes into the payload, because "no pass tonight" and "no pass above your
    tree line tonight" are different answers and a caller cannot tell them apart
    from an empty list."""
    cfg = _cfg()
    poly = getattr(cfg.safety, "horizon", None)
    if poly:
        # The drawn line IS the answer where it exists. ``horizon_min_deg`` is
        # the onboarding placeholder for a rig nobody has drawn a horizon for,
        # so applying it on top of a real polyline would raise a tree line the
        # operator measured for themselves.
        return list(poly), 0.0, "site_polyline"
    floor = float(getattr(cfg.site, "horizon_min_deg", 0.0) or 0.0)
    return None, floor, "horizon_min_deg" if floor > 0.0 else "none"


def no_rows_note(ids, state: dict) -> str:
    """The sentence for a search that produced no element sets to propagate.

    THREE DIFFERENT FACTS WEAR THE SAME EMPTY LIST, and telling a caller the
    wrong one sends them to the wrong screen:

    * nothing has ever been downloaded -- the rig needs a network and a refresh,
      which is what ``NO_SATELLITES_NOTE`` says;
    * the cache is there and the ids asked for are not in it -- refreshing
      changes nothing, because CelesTrak's bright-object group is a curated list
      and most catalogue numbers are simply not on it. The ids get NAMED, so the
      caller can see it was their filter and not the sky;
    * the cache is there and carries no usable rows at all, which is a broken
      file rather than an empty sky.

    Before this, an ``ids`` filter that matched nothing on a rig with 200 fresh
    element sets answered "No satellite elements have been downloaded yet",
    which was false in every particular."""
    if not state["present"]:
        return state["note"] or _elements.NO_SATELLITES_NOTE
    if ids:
        wanted = ", ".join(str(int(i)) for i in ids)
        return (f"No element set is cached for catalogue number {wanted}. "
                f"AstroDeck keeps CelesTrak's bright-object group (currently "
                f"{state['count']} objects) plus the ISS, Tiangong and Hubble, "
                f"so a satellite outside that list is not one it can place. "
                f"This is the filter, not the sky: clear it to see tonight's "
                f"passes.")
    return ("The satellite element file is present but carries no usable "
            "element sets. Refresh the elements from Sky settings while the "
            "rig is online.")


def floor_at(poly, floor_deg: float, az_deg: float) -> float:
    """The altitude a target must clear at azimuth ``az_deg``.

    ``interp_wrap`` is imported from the sequence scheduler rather than
    reimplemented: this app has exactly one idea of where the trees are."""
    from ...sequence.schedule import interp_wrap

    return max(floor_deg, float(interp_wrap(poly, az_deg)))


def _floor_table(poly, floor_deg: float) -> np.ndarray:
    n = int(round(360.0 / _FLOOR_TABLE_STEP_DEG))
    return np.array([floor_at(poly, floor_deg, i * _FLOOR_TABLE_STEP_DEG)
                     for i in range(n)], dtype=float)


def _floor_lookup(table: np.ndarray, az: np.ndarray) -> np.ndarray:
    idx = np.rint(az / _FLOOR_TABLE_STEP_DEG).astype(int) % table.size
    return table[idx]


# ------------------------------------------------------------- the sampled sky

class _Sky:
    """Everything about a time grid that does NOT depend on which satellite it
    is: the Sun's geocentric vector, the observer's Sun altitude, the site's
    ITRS position and the horizon table. Built once per request and reused
    across every satellite, which is most of what makes the search affordable."""

    def __init__(self, t0: float, hours: float):
        from astropy.time import Time

        from ..coords import sun_altaz

        self.loc, self.lat, self.lon = _sat._site_location()
        self.site_km = _sat.site_itrs_km(self.loc)
        self.poly, self.floor_deg, self.source = horizon_profile()
        self.table = _floor_table(self.poly, self.floor_deg)
        n = int(hours * 3600.0 / STEP_S) + 1
        self.unix = t0 + np.arange(n, dtype=float) * STEP_S
        self.t = Time(self.unix, format="unix")
        self.sun_km = _sat.sun_gcrs_km(self.t)
        self.sun_alt = np.array([sun_altaz(self.lat, self.lon, u)[0]
                                 for u in self.unix], dtype=float)
        self.dark = self.sun_alt < PASS_SUN_ALT_MAX_DEG

    def sample(self, sat, unix_times):
        """``(alt, az, range_km, sunlit)`` for one satellite at arbitrary
        instants -- used by the refinement, which needs a handful of points
        between grid samples."""
        from astropy.time import Time

        t = Time(np.atleast_1d(np.asarray(unix_times, dtype=float)),
                 format="unix")
        r_teme = _sat.propagate_teme(sat, t)
        itrs = _sat.teme_to_itrs_km(r_teme, t)
        gcrs = _sat.teme_to_gcrs_km(r_teme, t)
        alt, az, rng = _sat.altaz_from_itrs(itrs, self.site_km, self.lat,
                                            self.lon)
        lit = _sat.is_sunlit(_sat.shadow_state(gcrs, _sat.sun_gcrs_km(t)))
        return alt, az, rng, lit

    def margin(self, sat, unix_times) -> np.ndarray:
        """Altitude MINUS the exact horizon floor. Zero-crossings of this are
        the pass boundaries, and it is deliberately the exact ``interp_wrap``
        function rather than the lookup table."""
        alt, az, _rng, _lit = self.sample(sat, unix_times)
        floors = np.array([floor_at(self.poly, self.floor_deg, float(a))
                           for a in np.atleast_1d(az)], dtype=float)
        return np.atleast_1d(alt) - floors

    def visible_sign(self, sat, unix_times) -> np.ndarray:
        """+1 where ALL THREE conditions hold (up, sunlit, observer in the
        dark), -1 anywhere else.

        The sign function the visible window is bisected against, exactly as
        ``margin`` is for the horizon crossings and ``_shadow_edges``' own
        ``lit_sign`` is for the shadow. Whichever of the three flips is the one
        the crossing lands on, and over a ten-second bracket only one of them
        ever does."""
        from ..coords import sun_altaz

        times = np.atleast_1d(np.asarray(unix_times, dtype=float))
        alt, az, _rng, lit = self.sample(sat, times)
        floors = np.array([floor_at(self.poly, self.floor_deg, float(a))
                           for a in np.atleast_1d(az)], dtype=float)
        up = np.atleast_1d(alt) > floors
        dark = np.array([sun_altaz(self.lat, self.lon, float(u))[0]
                         for u in times], dtype=float) < PASS_SUN_ALT_MAX_DEG
        return np.where(up & np.atleast_1d(lit) & dark, 1.0, -1.0)


def _bisect(f, lo: np.ndarray, hi: np.ndarray, tol_s: float) -> np.ndarray:
    """Vectorised bisection of ``f`` between ``lo`` and ``hi``.

    ``f`` is evaluated on the whole array of midpoints at once -- one astropy
    transform per iteration instead of one per crossing, which is the
    difference between 5 ms and half a second per satellite."""
    lo = np.asarray(lo, dtype=float).copy()
    hi = np.asarray(hi, dtype=float).copy()
    if lo.size == 0:
        return lo
    f_lo = f(lo)
    span = float(np.max(np.abs(hi - lo)))
    steps = max(1, int(math.ceil(math.log2(max(tol_s, span) / tol_s))))
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        same = np.sign(f_mid) == np.sign(f_lo)
        lo = np.where(same, mid, lo)
        f_lo = np.where(same, f_mid, f_lo)
        hi = np.where(same, hi, mid)
    return 0.5 * (lo + hi)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous ``True`` runs as ``(first, last)`` index pairs."""
    if not mask.any():
        return []
    idx = np.flatnonzero(mask)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist()))


# ------------------------------------------------------------------ the search

def find_passes(hours: float = DEFAULT_HOURS, min_alt_deg: float = 0.0,
                ids=None, when: float | None = None) -> dict:
    """Every visible pass in the next ``hours``.

    Raises :class:`satellites.SatellitesUnavailable` when the site is unset --
    there is no geocentric stand-in for a pass prediction."""
    hours = max(0.1, min(float(hours), MAX_HOURS))
    t0 = _time.time() if when is None else float(when)
    notes: list[str] = []

    state = _elements.cache_state(_elements.SATELLITES, t0)
    rows = _sat.rows_by_id(ids) if ids else _sat.cached_rows()
    if not rows:
        # ``horizon_source`` IS ANSWERED HERE TOO. It is a fact about the rig's
        # configuration, not about this search: reporting "none" on a rig with a
        # drawn tree line, purely because the row filter matched nothing, is the
        # card telling the operator their horizon is not set up.
        return {"passes": [], "horizon_source": horizon_profile()[2],
                "elements": state,
                "notes": [no_rows_note(ids, state)]}
    if state["note"]:
        notes.append(state["note"])

    sky = _Sky(t0, hours)
    out: list[dict] = []
    for el in rows:
        try:
            sat = _sat._satrec(el)
            r_teme = _sat.propagate_teme(sat, sky.t)
        except _sat.SatellitesUnavailable as e:
            notes.append(f"{el.get('name')} was left out: {e}")
            continue
        itrs = _sat.teme_to_itrs_km(r_teme, sky.t)
        gcrs = _sat.teme_to_gcrs_km(r_teme, sky.t)
        alt, az, _rng = _sat.altaz_from_itrs(itrs, sky.site_km, sky.lat,
                                             sky.lon)
        lit = _sat.is_sunlit(_sat.shadow_state(gcrs, sky.sun_km))
        up = alt > _floor_lookup(sky.table, az)
        if not up.any():
            continue
        visible = up & lit & sky.dark
        age = max(0.0, (t0 - _sat.epoch_unix(sat)) / 86400.0)
        name = str(el.get("name") or f"NORAD {el.get('norad_id')}")
        for first, last in _runs(up):
            if not visible[first:last + 1].any():
                # Either the satellite spent the whole pass in the Earth's
                # shadow, or the Sun was still up here. Both are real passes and
                # neither is a SIGHTING, so neither is reported.
                continue
            peak_i = first + int(np.argmax(alt[first:last + 1]))
            if float(alt[peak_i]) < float(min_alt_deg):
                continue
            out.append(_one_pass(sky, sat, el, name, age, alt, az, lit,
                                 visible, first, last, peak_i))
    out.sort(key=lambda p: p["peak_unix"])
    return {"passes": out, "horizon_source": sky.source, "elements": state,
            "notes": notes}


def _one_pass(sky, sat, el, name, age, alt, az, lit, visible, first, last,
              peak_i) -> dict:
    """Refine one above-the-horizon run into a reported pass.

    TWO WINDOWS, AND THEY ARE NOT THE SAME WINDOW. Both are on the row because
    both are true and a caller needs to know which one it is reading:

    * ``start_unix`` / ``end_unix`` are the HORIZON CROSSINGS -- when the
      satellite clears the drawn tree line and when it drops back behind it.
      This is the pass, and it is what the geometry of the orbit says.
    * ``visible_start_unix`` / ``visible_end_unix`` are the window in which
      there is something to SEE: up AND sunlit AND the observer in the dark, the
      same three conditions that decided the pass was reportable at all. A pass
      that rises in the Earth's shadow or before the sky is dark begins minutes
      after ``start_unix``, and telling somebody to be outside then is telling
      them to watch an empty sky.

    They are computed the same way, which is the point: the visible edges are
    bisected to one second against ``_Sky.visible_sign`` exactly as the horizon
    crossings are against ``margin``. Where the pass is visible end to end the
    two pairs agree to within the refinement.

    A pass that enters the Earth's shadow half way through has ONE visible
    window here, from the first visible instant to the last, and
    ``enters_shadow_unix`` / ``leaves_shadow_unix`` name the gap inside it."""
    def margin(times):
        return sky.margin(sat, times)

    # ENTRY / EXIT, bisected to one second against the exact horizon. A pass
    # clipped by the edge of the search window keeps the window's own boundary
    # -- there is no crossing to find there, and inventing one would report a
    # rise that happened before the caller asked.
    start = float(sky.unix[first])
    if first > 0:
        start = float(_bisect(margin, np.array([sky.unix[first - 1]]),
                              np.array([sky.unix[first]]), REFINE_S)[0])
    end = float(sky.unix[last])
    if last + 1 < sky.unix.size:
        end = float(_bisect(margin, np.array([sky.unix[last + 1]]),
                            np.array([sky.unix[last]]), REFINE_S)[0])

    # PEAK, by ternary search on the same one-second resolution.
    lo = float(sky.unix[max(first, peak_i - 1)])
    hi = float(sky.unix[min(last, peak_i + 1)])
    peak_unix = _ternary_max(
        lambda times: sky.sample(sat, times)[0], lo, hi, REFINE_S)

    a0, z0, _r0, _l0 = sky.sample(sat, [start, peak_unix, end])
    lit_run = lit[first:last + 1]
    frac = float(np.count_nonzero(lit_run)) / float(max(1, lit_run.size))
    enters, leaves = _shadow_edges(sky, sat, lit, first, last)
    vis_start, vis_end = _visible_edges(sky, sat, visible, first, last,
                                        start, end)
    return {
        "norad_id": int(el.get("norad_id", 0)),
        "name": name,
        "start_unix": start,
        "peak_unix": peak_unix,
        "end_unix": end,
        "visible_start_unix": vis_start,
        "visible_end_unix": vis_end,
        "start_az": round(float(z0[0]), 1),
        "peak_az": round(float(z0[1]), 1),
        "end_az": round(float(z0[2]), 1),
        "max_alt_deg": round(float(a0[1]), 2),
        "duration_s": round(max(0.0, end - start), 1),
        "sunlit_fraction": round(frac, 3),
        "enters_shadow_unix": enters,
        "leaves_shadow_unix": leaves,
        "elements_age_days": round(age, 3),
    }


def _ternary_max(f, lo: float, hi: float, tol_s: float) -> float:
    """The time of maximum altitude, to ``tol_s``. Ternary search because the
    altitude curve over a pass is unimodal -- there is exactly one culmination
    between a rise and a set."""
    while hi - lo > tol_s:
        third = (hi - lo) / 3.0
        m1, m2 = lo + third, hi - third
        v = f([m1, m2])
        if float(v[0]) < float(v[1]):
            lo = m1
        else:
            hi = m2
    return 0.5 * (lo + hi)


def _visible_edges(sky, sat, visible, first, last, start, end):
    """``(visible_start_unix, visible_end_unix)`` for one pass.

    The first and last instants at which the satellite is up AND sunlit AND the
    observer is in the dark, bisected to one second the same way the horizon
    crossings are -- see ``_one_pass`` for why this is a different window from
    ``start_unix``/``end_unix`` and why both are reported.

    Clipped to the pass: a run that begins visible at the search window's own
    first sample has no crossing to find before it, and inventing one would
    report a sighting that started before the caller asked."""
    def sign(times):
        return sky.visible_sign(sat, times)

    run = np.flatnonzero(visible[first:last + 1])
    if run.size == 0:                   # not reachable: unseen passes are
        return start, end               # dropped before _one_pass is called
    v_first = first + int(run[0])
    v_last = first + int(run[-1])

    vis_start = float(sky.unix[v_first])
    if v_first > 0 and not bool(visible[v_first - 1]):
        vis_start = float(_bisect(sign, np.array([sky.unix[v_first - 1]]),
                                  np.array([sky.unix[v_first]]), REFINE_S)[0])
    vis_end = float(sky.unix[v_last])
    if v_last + 1 < sky.unix.size and not bool(visible[v_last + 1]):
        vis_end = float(_bisect(sign, np.array([sky.unix[v_last + 1]]),
                                np.array([sky.unix[v_last]]), REFINE_S)[0])
    # The visible window is INSIDE the pass by construction; the two refinements
    # are independent bisections, so clamp rather than let a sub-second
    # disagreement print a sighting that starts before the satellite rose.
    return max(vis_start, start), min(vis_end, end)


def _shadow_edges(sky, sat, lit, first, last):
    """``(enters_shadow_unix, leaves_shadow_unix)`` inside the pass, or None.

    Refined to a second the same way the horizon crossings are, on a sign
    function built from the boolean: a shadow edge a caller can see coming is
    the difference between "it will fade out overhead" and a satellite that
    just vanishes."""
    def lit_sign(times):
        return np.where(sky.sample(sat, times)[3], 1.0, -1.0)

    run = lit[first:last + 1]
    enters = leaves = None
    changes = np.flatnonzero(np.diff(run.astype(int)))
    for c in changes.tolist():
        i = first + c
        edge = float(_bisect(lit_sign, np.array([sky.unix[i]]),
                             np.array([sky.unix[i + 1]]), REFINE_S)[0])
        if run[c] and not run[c + 1] and enters is None:
            enters = edge
        elif not run[c] and run[c + 1] and leaves is None:
            leaves = edge
    return enters, leaves
