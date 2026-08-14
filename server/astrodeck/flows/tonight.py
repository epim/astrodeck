"""Tonight, resolved — the ephemeris the Tonight panel's three tabs render.

The prototype's ``tonight()`` (design handoff, README §7) invents its numbers:
dusk at 20:41, moonrise at 23:37, "banked" integration hard-coded as 35% of the
goal. The README says so out loud — "ephemeris times in the Tonight panel are
simulated placeholders — production computes them from ``catalog/visibility.py``
and ``sequence/schedule.py``". So the SHAPE below is the prototype's and every
NUMBER comes from those two modules.

WHAT THIS RETURNS IS TIMES, NOT PIXELS. The README puts the timeline's geometry
in the UI ("SVG viewBox 0 0 1000 150 … all labels live in an HTML layer") and
this function on the other side of the wire, so a server that emitted x/width
would be deciding the layout of a panel it cannot see. Timestamps are unix
seconds under ``*_unix`` names, matching ``visibility.py``'s contract, and no
clock string is formatted here: the operator's timezone is the browser's, and a
server that formats has already replaced it with its own (the #228 war story —
one window, printed in UTC, read as a different night).

TWO SOURCES OF NIGHT, BOTH ON PURPOSE:

* ``schedule.observing_night`` gives DUSK and DAWN — the operator's own imaging
  twilight, which is what the autorun window hangs off. It is the one answer to
  "when is tonight?" and re-deriving it here is how three surfaces came to
  disagree about one forecast.
* ``visibility.compute_night`` gives ASTRONOMICAL DARK, the altitude curves, the
  per-target windows and the moon. That is the deeper (−18°) boundary the
  timeline draws as a separate DARK tick, and the prototype draws both.

DETERMINISTIC given ``(plan, site, now)``. ``now`` defaults to the wall clock —
once, at the top — and every module below is then handed that instant
explicitly; ``compute_night`` in particular is pinned to a DATE derived from
``now`` rather than left to read the clock itself. Two calls with the same
arguments must agree, because the README makes the timeline "a rendering of the
compile, never a separate truth" and a timeline that shifted between renders
would be a third truth.

FAILS HONESTLY. No site, unreadable coordinates or polar day return
``ok: False`` with a sentence saying which, and no times at all — the house
convention ``observing_night`` set (None means "we do not know where the
observer is, so we must not pretend to know when their night is").
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..sequence.schedule import (hours_to_meridian_flip, observing_night,
                                 prev_sun_event)
from .compile import compile_plan
from .models import FlowGraph
from .nodes import parse_cycle_plan

#: Fallback imaging twilight when neither the caller nor the config has one.
#: Same number ``schedule.observing_night`` falls back to; duplicated rather
#: than imported because that one is a local inside a function.
DEFAULT_TWILIGHT_DEG = -12.0

#: The DUSK WINDOW node's ``minAlt`` default, and ``visibility``'s
#: ``DEFAULT_ALT_LIMIT``. Used when a flow has no dusk node to state a floor.
DEFAULT_MIN_ALT_DEG = 30.0

#: Altitude-curve resolution. 10 min is ``visibility.DEFAULT_STEP_MIN`` and puts
#: ~60 points under a winter night's arc — more than the 1000-unit-wide SVG can
#: resolve, and few enough that five pool candidates stay a small payload.
CURVE_STEP_MIN = 10

#: Story tones. The prototype hard-codes hex here; the README's do-not list
#: forbids that in production ("no hardcoded hex where a token exists"), so a
#: row names its tone and the UI maps it to the token ladder.
TONE_TEXT, TONE_DIM, TONE_FAINT = "text", "dim", "faint"
TONE_GOOD, TONE_WARN, TONE_BAD = "good", "warn", "bad"

_NO_SITE = ("No observatory site is set, so there is no night to resolve — "
            "nothing below would be about where you are.")
_BAD_SITE = ("This site's coordinates cannot be read as numbers, so no night "
             "can be resolved from them.")

#: Sun altitudes out of a DUSK FLATS ``window`` param ("Sun −2° … −8°"). The
#: typographic minus (U+2212) is what the node ships with, and an en/em dash is
#: what a re-typed one tends to become.
_ANGLE_RE = re.compile(r"([+\-−–—]?)\s*(\d+(?:\.\d+)?)\s*°")


# --------------------------------------------------------------- small readers

def _short(name: Any) -> str:
    """"M16 — Eagle" → "M16". The prototype's own split: the timeline block and
    the story sentence want the designation, not the essay."""
    return str(name or "").split(" —")[0].strip() or str(name or "").strip()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _signed(value: float, fmt: str = "g") -> str:
    """A number for the story, with the TYPOGRAPHIC minus the design uses.

    The prototype writes "sun −12°" and the DUSK FLATS node ships "Sun −2° …
    −8°"; a hyphen next to those in the same column reads as a different
    character, because it is."""
    return format(value, fmt).replace("-", "−")


def _norm_coord(text: Any) -> str:
    """Sexagesimal text ``catalog.coords`` can actually parse.

    ``parse_dec`` strips ``°``, ``'`` and ``"`` — but the TARGET node ships
    ``+41° 16′ 09″`` with the TYPOGRAPHIC prime and double-prime, which it does
    not strip, so it falls through to ``float()`` and raises on the vocabulary's
    OWN default value. Same trap for the typographic minus: ``parse_dec`` sniffs
    the sign with ``startswith("-")``, so ``−05°`` would have come back +5° —
    a target 10° from where the operator typed it. Normalised here rather than
    in ``coords.py`` because this module may not touch that file.
    """
    return (str(text or "")
            .replace("′", "'").replace("″", '"')
            .replace("−", "-").replace("–", "-").replace("—", "-")
            .strip())


def _twilight(twilight_deg: float | None) -> float:
    """The operator's imaging twilight, resolved to a NUMBER.

    ``observing_night`` accepts None and reads the config itself, but the story
    quotes the angle ("sun −12°"), so this resolver has to know it rather than
    pass the ambiguity along. One read, at the top, and every call below is
    given the resolved value."""
    if twilight_deg is not None:
        return float(twilight_deg)
    try:
        from ..config import config_store
        cfg = config_store.cfg()
        if cfg is not None:
            return float(cfg.safety.twilight_deg)
    except Exception:       # noqa: BLE001 - a config that cannot be read is not
        pass                # a reason to have no timeline; the default is honest
    return DEFAULT_TWILIGHT_DEG


def _site_dict(site: Any) -> tuple[dict | None, str]:
    """``({latitude, longitude, elevation_m}, "")`` or ``(None, why-not)``.

    Reads a hub-style dict OR a pydantic ``Site`` — both callers exist and
    neither should have to convert, the same reasoning ``observing_night``
    documents. Returns a plain dict because ``visibility.compute_night``
    subscripts it."""
    get = site.get if isinstance(site, dict) else (
        lambda k, d=None: getattr(site, k, d))
    if get("is_default", False):
        return None, _NO_SITE
    try:
        lat = float(get("latitude", 0.0) or 0.0)
        lon = float(get("longitude", 0.0) or 0.0)
        elev = float(get("elevation_m", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None, _BAD_SITE
    return {"latitude": lat, "longitude": lon, "elevation_m": elev,
            "is_default": False}, ""


def _night_date(now: float, lon_deg: float) -> str:
    """The evening's civil date, so ``compute_night`` is pinned instead of
    reading the clock.

    ``visibility._night_anchor_unix`` calls ``time.time()`` when given no date —
    which would make this whole resolver's answer depend on when it ran, not on
    the ``now`` it was handed, and the two would silently disagree for anyone
    rendering a past night. This reproduces that function's own "tonight" branch
    from ``now`` (local SOLAR time, longitude-only — there is no tz database in
    this app) and names the resulting night by its evening date, which is the
    key ``compute_night`` turns back into the same anchor.
    """
    lon_offset = lon_deg / 15.0 * 3600.0        # +E ⇒ local solar ahead of UTC
    local = now + lon_offset
    frac = (local % 86400.0) / 86400.0
    # local morning ⇒ this night's solar midnight has passed; afternoon ⇒ ahead.
    anchor_local = (local - frac * 86400.0 if frac < 0.5
                    else local + (1.0 - frac) * 86400.0)
    evening = anchor_local - 43200.0            # the noon before that midnight
    return _dt.datetime.fromtimestamp(
        evening, _dt.timezone.utc).date().isoformat()


# ------------------------------------------------------------------- the ledger

def _field(obj: Any, name: str, default: Any = None) -> Any:
    """One reader for both shapes. ``list_reports`` hands back dicts and
    ``load`` hands back models; a caller holding one should not have to know."""
    return obj.get(name, default) if isinstance(obj, dict) else getattr(
        obj, name, default)


def banked_hours_from_reports(reports: Iterable[Any],
                              targets: Iterable[str] | None = None,
                              ) -> dict[str, float]:
    """Hours of accepted light integration per filter, summed over reports.

    The BUDGET row's "4.2 h banked", and the only honest source for it is
    ``report.py``'s append-only ledger, which is on disk. This function is the
    pure half — fold a sequence of ``SessionReport``-shaped things into a
    mapping — so the route can supply the impure half (``SessionReporter.load``
    over ``list_reports()``) and this module can stay callable without one.

    ``targets``, when given, restricts the sum to reports' per-target
    breakdowns for those names.

    TODO(flows-handoff): whose Ha counts toward THIS flow's Ha goal? The README
    says "integration ledger per filter (banked vs goal)", which is what the
    default does — every Ha hour in the archive, whatever it was pointed at.
    That is wrong for anyone who shoots two Ha projects: M16's hours would fill
    M31's bar and the flow would stop asking for frames it still needs. Passing
    ``targets`` gives the per-target reading; which one the endpoint should use
    is the design question, and inventing a third is not this module's call.
    """
    wanted = {str(t) for t in targets} if targets is not None else None
    out: dict[str, float] = {}

    def fold(rows: Any) -> None:
        for row in rows or ():
            filt = _field(row, "filter")
            if not filt:
                continue
            out[str(filt)] = out.get(str(filt), 0.0) + _num(
                _field(row, "integration_s", 0.0)) / 3600.0

    for rep in reports or ():
        if wanted is None:
            fold(_field(rep, "by_filter"))
            continue
        for tb in _field(rep, "targets") or ():
            if str(_field(tb, "name", "")) in wanted:
                fold(_field(tb, "by_filter"))
    return out


# --------------------------------------------------------------- target coords

def catalog_coords(name: str, when: float | None = None
                   ) -> tuple[float, float] | None:
    """``(ra_hours, dec_deg)`` for a target NAME, from the shipped catalogue.

    A TARGET POOL compiles to names and constraints and NOTHING ELSE — the
    prototype's pool members are "M16, M17, M8, NGC 6946" — so without a lookup
    the best-of-four example has four targets and not one altitude curve. The
    catalogue is local, static data, so this stays deterministic; it is
    imported lazily and injectable (``resolve_name``) so a caller without the
    catalogue, or a test, is not forced through it.
    """
    try:
        from ..catalog.objects import search
        rows = search(str(name), limit=1, when=when).rows
    except Exception:       # noqa: BLE001 - a missing/broken catalogue means we
        return None         # do not know where this is, not a 500 for the panel
    if not rows:
        return None
    row = rows[0]
    try:
        return float(row["ra_hours"]), float(row["dec_deg"])
    except (KeyError, TypeError, ValueError):
        return None


def _target_coords(entry: dict, resolver: Callable[[str], tuple[float, float] | None],
                   ) -> tuple[tuple[float, float] | None, str]:
    """``((ra_hours, dec_deg), source)`` for one compiled target entry.

    Typed coordinates win: they are what the operator put in the node, and a
    catalogue hit for a similar name would quietly image somewhere else. Only
    when they are absent (a pool member) or unparseable do we fall back to the
    name, and the SOURCE rides along so the panel can say which it used.
    """
    from ..catalog.coords import parse_dec, parse_ra
    ra_text, dec_text = _norm_coord(entry.get("ra")), _norm_coord(entry.get("dec"))
    if ra_text and dec_text:
        try:
            return (parse_ra(ra_text), parse_dec(dec_text)), "node"
        except (ValueError, TypeError):
            pass            # falls through to the name — and says "catalog"
    hit = resolver(str(entry.get("name") or ""))
    return (hit, "catalog") if hit else (None, "")


# ------------------------------------------------------------------ dusk flats

def _flats_window(window_text: str, lat: float, lon: float,
                  dusk: float) -> tuple[float | None, float | None]:
    """The sun-altitude band a DUSK FLATS node names, as two timestamps.

    The node stores its window as display text ("Sun −2° … −8°") because that
    is what the inspector shows, so the two angles are read back out of it. Both
    crossings are SETTING crossings BEFORE dusk — flats are shot while the sky
    is still bright — so the search walks backwards from the resolved dusk,
    which also bounds it (``prev_sun_event`` is polar-safe by construction).

    Returns ``(None, None)`` when the text does not name two negative sun
    altitudes. A flats block drawn at a guessed time would be worse than none:
    the operator would plan the evening around it.

    TODO(flows-handoff): the DUSK FLATS window is a display STRING in the node
    contract ("Sun −2° … −8°"), so its two angles have to be read back out of
    prose that an operator can retype into anything. Two numeric params would
    make this exact; the vocabulary is the handoff's to change, not mine.
    """
    angles: list[float] = []
    for sign, mag in _ANGLE_RE.findall(window_text or ""):
        angles.append(-_num(mag) if sign else _num(mag))
    if len(angles) != 2:
        return None, None
    hi, lo = max(angles), min(angles)
    if hi > 0.0 or lo >= hi:
        return None, None
    start = prev_sun_event(lat, lon, hi, dusk, rising=False)
    end = prev_sun_event(lat, lon, lo, dusk, rising=False)
    if start is None or end is None or end <= start:
        return None, None
    return start, end


# ----------------------------------------------------------------- the resolver

def resolve_tonight(plan: dict | FlowGraph, site: Any, *,
                    name: str = "",
                    now: float | None = None,
                    twilight_deg: float | None = None,
                    banked: Callable[[], Mapping[str, float]] | None = None,
                    frames_by_target: Callable[
                        [], Mapping[str, Mapping[str, int]]] | None = None,
                    resolve_name: Callable[[str], tuple[float, float] | None] | None = None,
                    step_min: int = CURVE_STEP_MIN) -> dict:
    """Everything the Tonight panel draws, for one flow, at one instant.

    ``plan`` is a compiled plan (``compile.compile_plan``) or the graph itself,
    which is compiled here — because the README says the timeline is a rendering
    of the compile, and a caller that could hand this function a graph WITHOUT
    compiling it would be able to render a night the run would not run.

    ``banked`` is the session ledger, injected: hours already in the bank per
    filter. It is a callable and not a mapping so the route can read
    ``captures/reports`` lazily and this function can stay pure; the default is
    NO LEDGER, and a budget row then says the goal and tonight's contribution
    and explicitly does not claim a banked figure.

    Returns ``{ok, reason, now_unix, twilight_deg, night, flats, moon, targets,
    budget, story}``; on failure ``ok`` is False, ``reason`` says why in a
    sentence, and every time-bearing key is None/empty rather than plausible.
    """
    t_now = time.time() if now is None else float(now)
    graph = plan if isinstance(plan, FlowGraph) else None
    plan_dict: dict = compile_plan(graph, name) if graph is not None else dict(plan or {})
    tw = _twilight(twilight_deg)
    resolver = resolve_name or (lambda n: catalog_coords(n, t_now))

    sd, why = _site_dict(site)
    if sd is None:
        return _cannot(why, t_now, tw)
    lat, lon = sd["latitude"], sd["longitude"]

    pair = observing_night(sd, tw, t_now)
    if pair is None:
        return _cannot(
            f"The sun never crosses {_signed(tw)}° from this site tonight — "
            f"there is no night here to lay out, so there are no times to show.",
            t_now, tw)
    dusk, dawn = pair

    sched = plan_dict.get("schedule") or {}
    automation = plan_dict.get("automation") or {}
    offset_min = _num(sched.get("start_offset_min"))
    start_mode = str(sched.get("start_mode") or "now")
    min_alt = _num(sched.get("min_altitude_deg"), DEFAULT_MIN_ALT_DEG)

    # The autorun window, as the ENGINE will resolve it: dusk plus the node's
    # offset, closing at dawn only when the flow says stop at dawn.
    window_start = dusk + offset_min * 60.0 if start_mode == "dusk" else t_now
    window_stop = dawn if str(sched.get("stop_mode") or "") == "dawn" else None

    # Lazy: visibility pulls astropy, FastAPI and the hub. Importing the flows
    # package should not drag a router in behind it.
    from ..catalog.visibility import compute_night
    date = _night_date(t_now, lon)

    targets: list[dict] = []
    dark_start = dark_end = None
    darkness_kind = ""
    moon: dict | None = None
    for entry in plan_dict.get("targets") or []:
        coords, source = _target_coords(entry, resolver)
        label = _short(entry.get("name"))
        floor = _num(entry.get("min_altitude_deg"), min_alt)
        if coords is None:
            # Named but unplaceable. Listed anyway — the operator asked for it
            # and a silently-dropped target is how a night comes up short —
            # with nothing pretending to be an ephemeris.
            targets.append({
                "name": entry.get("name"), "label": label, "coords_from": "",
                "ra_hours": None, "dec_deg": None, "resolved": False,
                "min_altitude_deg": floor, "pool_rank": entry.get("pool_rank"),
                "window": None, "curve": [], "transit_unix": None,
                "transit_alt": None, "transit_in_daylight": None,
                "meridian_flip_unix": None, "moon_sep_deg": None,
                "never_rises": None,
            })
            continue
        ra_h, dec_d = coords
        night = compute_night(ra_h, dec_d, date=date, step_min=step_min,
                              alt_limit=floor, site=sd)
        dark_start, dark_end = night["dark_start_unix"], night["dark_end_unix"]
        darkness_kind = night["darkness_kind"]
        if moon is None:
            # Illumination/phase/rise/set do not depend on the target; only the
            # separation does, and that stays per-target below. Quoted at this
            # target's transit, which moves the illuminated fraction by well
            # under a percent across one night.
            moon = {k: v for k, v in night["moon"].items()
                    if k != "separation_deg"}
        bw = night["best_window"]
        targets.append({
            "name": entry.get("name"), "label": label, "coords_from": source,
            "ra_hours": ra_h, "dec_deg": dec_d, "resolved": True,
            "min_altitude_deg": floor, "pool_rank": entry.get("pool_rank"),
            "window": ({"start_unix": bw["start_unix"],
                        "end_unix": bw["end_unix"],
                        "mean_alt": bw["mean_alt"]} if bw else None),
            "curve": [[s["t_unix"], s["alt"]] for s in night["samples"]
                      if dusk <= s["t_unix"] <= dawn],
            "transit_unix": night["transit_unix"],
            "transit_alt": night["transit_alt"],
            "transit_in_daylight": night["transit_in_daylight"],
            "meridian_flip_unix": _flip_unix(ra_h, lon, t_now, dusk, dawn),
            "moon_sep_deg": night["moon"]["separation_deg"],
            "never_rises": night["never_rises_above_limit"],
        })

    flats = None
    if automation.get("dusk_flats"):
        df = automation["dusk_flats"]
        f0, f1 = _flats_window(str(df.get("window") or ""), lat, lon, dusk)
        flats = {"start_unix": f0, "end_unix": f1,
                 "window": df.get("window"), "adu_target": df.get("adu_target"),
                 "count": df.get("count"), "method": df.get("method")}

    budget = _budget(plan_dict, banked)

    out = {
        "ok": True,
        "reason": "",
        "now_unix": t_now,
        "twilight_deg": tw,
        "night": {
            "dusk_unix": dusk, "dawn_unix": dawn,
            "window_start_unix": window_start, "window_stop_unix": window_stop,
            "dark_start_unix": dark_start, "dark_end_unix": dark_end,
            "darkness_kind": darkness_kind,
        },
        "flats": flats,
        "moon": moon,
        "targets": targets,
        "budget": budget,
        # The CAMPAIGN tab and the STORY tab's brief. Both read the GRAPH, not
        # the compile, so both are empty when a caller hands in a plan dict -
        # the same rule the dawn story already follows for the report sink.
        "campaign": _campaign(graph, frames_by_target),
        "brief": brief(graph),
    }
    out["story"] = _story(out, plan_dict, graph)
    return out


def _cannot(reason: str, t_now: float, tw: float) -> dict:
    """The "we cannot compute this" answer, in the shape callers already parse.

    Every time-bearing key is None or empty on purpose. The alternative — a
    plausible dusk from a default site — is the failure this whole panel exists
    to avoid: an operator planning an evening around a number nobody measured.
    """
    return {
        "ok": False, "reason": reason, "now_unix": t_now, "twilight_deg": tw,
        "night": None, "flats": None, "moon": None, "targets": [], "budget": [],
        "story": [{"t_unix": None, "label": "—", "msg": reason, "tone": TONE_WARN}],
    }


def _flip_unix(ra_hours: float, lon: float, t_now: float,
               dusk: float, dawn: float) -> float | None:
    """When this target crosses the meridian TONIGHT, or None.

    ``hours_to_meridian_flip`` is the engine's own countdown (and the hub's), so
    the timeline's FLIP tick and the flip the mount actually performs come from
    one piece of arithmetic. Non-positive means the crossing already happened —
    the next one is a sidereal day away, i.e. not tonight — and a crossing
    outside [dusk, dawn] is not on tonight's timeline either. None in both
    cases: no tick beats a tick at the wrong hour.
    """
    hours = hours_to_meridian_flip(ra_hours, lon, t_now)
    if hours <= 0.0:
        return None
    flip = t_now + hours * 3600.0
    return flip if dusk <= flip <= dawn else None


# ---------------------------------------------------------------- the ledger row

def _budget(plan: dict, banked: Callable[[], Mapping[str, float]] | None
            ) -> list[dict]:
    """Banked-vs-goal integration per filter, plus what tonight adds.

    ONE ROW PER DISTINCT STEP, not per target. ``compile_plan`` copies every
    capture step onto every target, so a pool of four candidates carries four
    copies of one Ha loop — and only ONE of them is shot on any given night
    (that is what "best available" means). Summing per target would promise
    four times the integration the rig can deliver, which is the direction of
    error that costs a project a week.
    """
    have_ledger = banked is not None
    bank: Mapping[str, float] = {}
    if banked is not None:
        try:
            bank = banked() or {}
        except (OSError, ValueError):
            # A ledger on disk that cannot be read is a missing ledger, not a
            # blank timeline. Say "no ledger" rather than "0 h banked" — those
            # are different claims and only one of them is true.
            bank, have_ledger = {}, False

    seen: set[tuple] = set()
    rows: list[dict] = []
    for target in plan.get("targets") or []:
        for step in target.get("steps") or []:
            goal = _num(step.get("integration_goal_h"))
            if goal <= 0.0:
                continue        # 0 means "no goal", per the compile's own rule
            sig = (step.get("filter"), step.get("exposure_s"),
                   step.get("gain"), step.get("binning"), step.get("count"),
                   goal)
            if sig in seen:
                continue
            seen.add(sig)
            filt = str(step.get("filter") or "—")
            tonight_h = _num(step.get("exposure_s")) * _num(step.get("count")) / 3600.0
            rows.append({
                "filter": filt,
                "goal_h": round(goal, 2),
                "banked_h": (round(_num(bank.get(filt)), 2) if have_ledger
                             else None),
                "tonight_h": round(tonight_h, 2),
                "has_ledger": have_ledger,
            })
    return rows


# ------------------------------------------------------------------- the story

def _first(graph: FlowGraph | None, node_type: str):
    """The first node of a type, or None. The brief asks this fifteen times."""
    if graph is None:
        return None
    for n in graph.nodes:
        if n.type == node_type:
            return n
    return None


def _wired(graph: FlowGraph | None, *, frm=None, from_port: str | None = None,
           to=None, to_port: str | None = None) -> bool:
    """Is there an edge matching every constraint given?"""
    if graph is None:
        return False
    for e in graph.edges:
        if frm is not None and e.from_ != frm.id:
            continue
        if to is not None and e.to != to.id:
            continue
        if from_port is not None and e.fromPort != from_port:
            continue
        if to_port is not None and e.toPort != to_port:
            continue
        return True
    return False


#: How the DUSK WINDOW's `start` reads inside a sentence.
_START_PROSE = {
    "Astro dusk": "astronomical dusk",
    "Nautical dusk": "nautical dusk",
    "Civil dusk": "civil dusk",
    "Clock time": "the set clock time",
}


def _join_and(parts: list[str]) -> str:
    """``a, b and c``. The prototype's regex, spelled out."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def brief(graph: FlowGraph | None) -> str:
    """The STORY tab's mechanical brief: the graph, read back as prose.

    "Generated deterministically from the graph, sentence per capability, params
    inlined verbatim" - so edit a param and the sentence changes, and a sentence
    appears only when the node that earns it exists.

    WHY IT EARNS ITS PLACE next to a canvas that already shows the same thing:
    the canvas shows the SHAPE and the inspector shows ONE node's params. Nothing
    else puts every number the night will actually use into one paragraph an
    operator can read at 21:00 and disagree with. A graph that reads wrong out
    loud usually is wrong.

    IT IS NOT A SUMMARY OF THE COMPILE, and that distinction is the honest one:
    it describes what the GRAPH says, which is what the operator drew. Where the
    compile drops something (the `unmapped` list), the brief will still describe
    it - that gap is the compile's to report, and it does, rather than this
    quietly omitting a stage the operator can plainly see on the canvas.
    """
    if graph is None:
        return ""
    g = graph.with_defaults()
    n = lambda t: _first(g, t)                                   # noqa: E731
    dusk, pool, tgt = n("dusk"), n("pool"), n("target")
    cyc, cap, rep = n("cycle"), n("capture"), n("report")
    cw, hold, cq, pc = n("cloudwatch"), n("holdresume"), n("calib"), n("parkclose")
    saf, dome, df = n("safety"), n("dome"), n("duskflats")
    guide, af, slew = n("guide"), n("autofocus"), n("slew")
    seg: list[str] = []

    if dusk is not None:
        p = dusk.params
        off = int(_num(p.get("offset")))
        start = _START_PROSE.get(str(p.get("start")), str(p.get("start")))
        t = f"This flow arms at {start}"
        if off:
            t += f" ({_signed(off, '+.0f')} min)"
        if dome is not None:
            t += ", opens the dome and binds it to the mount"
        if df is not None:
            t += (f", and shoots {df.params.get('count')} flats per filter "
                  f"({str(df.params.get('method')).lower()}) in the twilight window")
        seg.append(t + ".")

    # "IT THEN" NEEDS SOMETHING TO FOLLOW. The prototype opens this sentence
    # with a fixed "It then", which reads correctly after the arming sentence
    # and is broken English without one - the EAA example has no DUSK WINDOW, so
    # its brief began "It then arms M27 - Dumbbell." with no antecedent. Same
    # sentence, same content, correct connective; noted in the milestone summary
    # as a prototype defect rather than a design change.
    lead = "It then " if seg else "This flow "
    if pool is not None:
        p = pool.params
        seg.append(f"{lead}selects the best of {p.get('members')} - above "
                   f"{p.get('minAlt')}°, at least {p.get('moonSep')}° from the "
                   f"moon (if up), within {p.get('maxHA')} h of the meridian.")
    elif tgt is not None:
        seg.append(f"{lead}arms {tgt.params.get('name')}.")

    rig: list[str] = []
    if slew is not None:
        rig.append(f"slews and plate-solves to within {slew.params.get('tol')}′ "
                   f"({slew.params.get('solver')})")
    if af is not None:
        rig.append(f"autofocuses ({str(af.params.get('method')).lower()})")
    if guide is not None:
        rig.append(f"guides with {guide.params.get('provider')} (settle below "
                   f"{guide.params.get('settle')}″, dither every "
                   f"{guide.params.get('dither')} frames)")
    if rig:
        seg.append("For each target it " + ", ".join(rig) + ".")

    if cyc is not None:
        slots = parse_cycle_plan(cyc.params.get("plan"))
        table = ", ".join(f"{f} {e} s × {cyc.params.get('cycles')}"
                          for f, e in slots)
        seg.append(f"Capture interleaves one sub per filter per pass - {table} - "
                   f"so every channel grows evenly; a sub is graded and only "
                   f"counts below HFR {cyc.params.get('reject')}″.")
    elif cap is not None:
        p = cap.params
        seg.append(f"It captures {p.get('filter')} {p.get('exposure')} s × "
                   f"{p.get('count')} (gain {p.get('gain')}, bin {p.get('bin')}); "
                   f"subs grading above HFR {p.get('reject')}″ don't count.")

    advances = pool is not None and _wired(g, to=pool, to_port="advance")
    if rep is not None and advances:
        seg.append("When a target's quota is met, a session report is cut and "
                   "the pool advances to the next best - finished targets are "
                   "never re-selected.")
    elif rep is not None:
        seg.append("A session report is appended when the run ends.")

    if pool is not None and (_wired(g, frm=pool, from_port="floor")
                             or str(pool.params.get("onFloor") or "")
                             .startswith("Advance")):
        seg.append(f"If the active target sinks to the "
                   f"{pool.params.get('minAlt')}° floor, it is set aside - "
                   f"resumed the next night, not retried tonight - and the next "
                   f"best takes over.")

    if cw is not None:
        t = (f"If cloud cover above {cw.params.get('threshold')}% is detected, "
             f"imaging pauses at the frame boundary")
        if cq is not None:
            t += (" and the calibration queue banks whatever the library lacks "
                  "(darks → bias → flats-if-panel)")
        t += (f"; once the sky holds clear for {cw.params.get('clearFor')} min it")
        steps: list[str] = []
        if hold is not None:
            hp = hold.params
            # THE COOLER SENTENCE COMES FIRST because the checklist does, and
            # the brief's job is to let an operator notice that it is missing.
            if str(hp.get("cooler") or "").startswith("Re-cool"):
                steps.append("re-cools the sensor to setpoint and waits for it "
                             "to stabilize")
            steps.append("restores the filter")
            if str(hp.get("recenter") or "").startswith("Re-center"):
                steps.append("re-centers")
            if hp.get("refocus") != "Never":
                steps.append("refocuses" if hp.get("refocus") == "Always"
                             else "refocuses if drifted")
            steps.append("resumes at the same slot")
        else:
            steps.append("resumes")
        seg.append(f"{t} {_join_and(steps)}.")

    if (pc is not None and dusk is not None
            and _wired(g, frm=dusk, from_port="nightend", to=pc)):
        t = (f"When astronomical night ends, the mount parks and the "
             f"{str(pc.params.get('closure')).lower()} closes")
        if str(pc.params.get("cooler") or "").startswith("Hold"):
            t += " with the cooler held cold"
        if cq is not None and _wired(g, frm=pc, to=cq):
            t += ", banking capped day darks"
        if str(dusk.params.get("repeat") or "Single night") != "Single night":
            t += ("; the flow re-arms at the next dusk and resumes mid-cycle "
                  "from the ledger")
        seg.append(t + ".")

    if saf is not None:
        seg.append("Rain, wind, or power failure aborts and parks "
                   "unconditionally - a stale reading counts as unsafe.")

    if (dusk is not None and pool is not None
            and dusk.params.get("repeat") == "Nightly until pool complete"):
        members = [m for m in str(pool.params.get("members") or "").split(",")
                   if m.strip()]
        seg.append(f"Once all {len(members)} targets hold their "
                   f"{pool.params.get('quota')}-cycle quota, the rig stays parked.")

    return " ".join(seg)


def frames_by_target_from_reports(reports: Iterable[Any]
                                  ) -> dict[str, dict[str, int]]:
    """``{target: {filter: accepted_frames}}``, summed over the ledger.

    The pure half of the CAMPAIGN tab's progress, matching
    ``banked_hours_from_reports`` in shape and for the same reason: the route
    supplies the impure half and this module stays callable without a disk.

    ACCEPTED FRAMES, not captured. A rejected sub is one the night has to shoot
    again, so counting it toward a quota would retire a target that still owes
    work - and on a campaign that error compounds across every remaining night.
    """
    out: dict[str, dict[str, int]] = {}
    for rep in reports or ():
        for tb in _field(rep, "targets") or ():
            name = str(_field(tb, "name", "") or "")
            if not name:
                continue
            bucket = out.setdefault(name, {})
            for fb in _field(tb, "by_filter") or ():
                filt = _field(fb, "filter")
                if not filt:
                    continue
                bucket[str(filt)] = bucket.get(str(filt), 0) + int(
                    _num(_field(fb, "frames", 0)))
    return out


def _campaign(graph: FlowGraph | None,
              frames_by_target: Callable[[], Mapping[str, Mapping[str, int]]] | None
              ) -> dict:
    """The CAMPAIGN tab: how much of the pool's quota each member has banked.

    Returns ``{is_campaign, has_pool, has_ledger, quota, members[], note}``.
    A member row is ``{name, banked, quota, done, pct}``.

    A CYCLE IS THE UNIT, and it is complete only when EVERY slot in the table
    has its subs. So a member's banked cycles is the MINIMUM over the slots of
    (accepted frames ÷ subs-per-pass), not the total frame count: forty-five L
    and no Ha is zero complete cycles of an LRGBSHO table, and reporting it as
    "45/45 - DONE" would retire a target that has one channel.

    NO LEDGER MEANS NO NUMBER. `has_ledger` false and every `banked` is None -
    NOT zero. "0 of 45 banked" and "nobody has looked" are different sentences,
    and only one of them should make an operator re-plan a month.

    NO PROJECTED COMPLETION DATE. The design asks for "pool complete in ~6 clear
    nights, weather-modelled" and the prototype hardcodes the 6; nothing on this
    server forecasts clear nights that far out, and the weather integration is a
    tonight-scale nowcast. A number invented here would be the most quotable
    thing on the screen and the least true, so the note states the work
    REMAINING - which is known exactly - and says the nights are not forecast.
    """
    g = graph.with_defaults() if graph is not None else None
    pool = _first(g, "pool")
    dusk = _first(g, "dusk")
    cyc = _first(g, "cycle")
    is_campaign = bool(
        pool is not None and dusk is not None
        and str(dusk.params.get("repeat") or "Single night") != "Single night")

    if pool is None:
        return {"is_campaign": False, "has_pool": False, "has_ledger": False,
                "quota": 0, "members": [],
                "note": "No target pool in this flow - campaigns need one."}

    quota = max(1, int(_num(pool.params.get("quota"), 45)))
    names = [m.strip() for m in str(pool.params.get("members") or "").split(",")
             if m.strip()]
    slots = parse_cycle_plan(cyc.params.get("plan")) if cyc is not None else []
    per_pass = (max(1, int(_num(cyc.params.get("perCycle"), 1)))
                if cyc is not None else 1)

    bank: Mapping[str, Mapping[str, int]] = {}
    has_ledger = frames_by_target is not None
    if frames_by_target is not None:
        try:
            bank = frames_by_target() or {}
        except Exception:
            has_ledger = False

    def cycles_for(name: str) -> int | None:
        if not has_ledger:
            return None
        got = bank.get(name) or {}
        if not slots:
            # No cycle stage: there is no "pass" to count, so the only honest
            # answer is that this flow's progress is not measured in cycles.
            return None
        return min(int(got.get(f, 0)) // per_pass for f, _ in slots)

    members = []
    for name in names:
        banked = cycles_for(name)
        members.append({
            "name": name,
            "banked": banked,
            "quota": quota,
            "done": banked is not None and banked >= quota,
            "pct": (min(100, round(100 * banked / quota)) if banked is not None
                    else None),
        })

    if not is_campaign:
        note = ("Single-night flow - set DUSK WINDOW → Repeat to make this a "
                "campaign.")
    elif not has_ledger:
        note = ("No session ledger available, so nothing here claims a banked "
                "figure. Dawn parks + closes; the cooler stays cold for day "
                "darks; each dusk resumes mid-cycle.")
    elif not slots:
        note = ("This campaign's capture stage is not a FILTER CYCLE, so "
                "progress is not counted in cycles. Dawn parks + closes; each "
                "dusk resumes where the ledger left off.")
    else:
        left = sum(max(0, quota - (m["banked"] or 0)) for m in members)
        passes = left * per_pass * len(slots)
        note = (f"{left} cycles left across the pool ({passes} subs). Nights to "
                f"finish are not forecast - clear-sky prediction that far out is "
                f"not something this rig models. Dawn parks + closes; the cooler "
                f"stays cold for day darks; each dusk resumes mid-cycle.")

    return {"is_campaign": is_campaign, "has_pool": True,
            "has_ledger": has_ledger, "quota": quota, "members": members,
            "note": note}


def _story(out: dict, plan: dict, graph: FlowGraph | None) -> list[dict]:
    """The STORY tab: the night as sentences, in the prototype's order.

    Rows carry ``t_unix`` and a ``label``; the label is "" for a timed row and
    "ANY"/"BUDGET" for the two special kinds, so the panel's time column shows
    a clock or a keyword without this module ever formatting one (see the
    module docstring on whose timezone that is).
    """
    night, automation = out["night"], (plan.get("automation") or {})
    sched = plan.get("schedule") or {}
    dusk, dawn = night["dusk_unix"], night["dawn_unix"]
    tw = out["twilight_deg"]
    timed: list[dict] = []
    rules: list[dict] = []

    def row(t_unix, msg, tone=TONE_TEXT, label=""):
        return {"t_unix": t_unix, "label": label, "msg": msg, "tone": tone}

    # 1. the window opens
    if str(sched.get("start_mode") or "") == "dusk":
        offset = _num(sched.get("start_offset_min"))
        applied = (f", {_signed(offset, '+.0f')} min offset applied" if offset
                   else "")
        timed.append(row(night["window_start_unix"],
                         f"Autorun window opens (sun {_signed(tw)}°{applied})"))
    else:
        timed.append(row(out["now_unix"],
                         "No dusk window in this flow — the run starts when you "
                         "press RUN, and stops when you stop it", TONE_DIM))

    # 2. the dome, which is not the flow's to countermand
    if automation.get("dome"):
        timed.append(row(night["window_start_unix"],
                         "Dome shutter opens, azimuth bound to the mount - any "
                         "unsafe or stale safety reading closes it, whatever the "
                         "flow is doing", TONE_DIM))

    # 3. dusk flats, if the sky is bright enough for long enough
    flats = out["flats"]
    if flats:
        adu = flats.get("adu_target")
        adu_txt = f"{int(_num(adu)):,}".replace(",", " ") if adu else "target"
        if flats["start_unix"] is not None and flats["end_unix"] is not None:
            mins = int(round((flats["end_unix"] - flats["start_unix"]) / 60.0))
            timed.append(row(
                flats["start_unix"],
                f"Flats window ({flats['window']}, about {mins} min): "
                f"{str(flats['method']).lower()} flats, exposure solved to "
                f"{adu_txt} ADU per filter", TONE_DIM))
        else:
            timed.append(row(
                dusk, f"Dusk flats are configured for \"{flats['window']}\" — "
                f"that window does not name two sun altitudes, so its clock "
                f"times are not resolved here", TONE_WARN))

    # 4. astronomical darkness (the deeper boundary, separate from dusk)
    if night["dark_start_unix"] is not None:
        kind = night["darkness_kind"]
        timed.append(row(night["dark_start_unix"],
                         "Astronomical darkness" if kind == "astronomical"
                         else f"Darkest the sky gets tonight ({kind})",
                         TONE_FAINT))

    # 5. what gets imaged
    timed.extend(_target_rows(out))

    # 6. the moon
    moon = out["moon"]
    if moon:
        pct = int(round(_num(moon.get("illumination")) * 100))
        seps = [t["moon_sep_deg"] for t in out["targets"]
                if t.get("moon_sep_deg") is not None]
        near = f", {min(seps):.0f}° from the nearest target" if seps else ""
        if moon.get("rise_unix"):
            timed.append(row(moon["rise_unix"],
                             f"Moon rises, {pct}% illuminated{near} — narrowband "
                             f"shrugs it off; broadband loses contrast",
                             TONE_FAINT))
        if moon.get("set_unix"):
            timed.append(row(moon["set_unix"],
                             "Moon sets — the rest of the night is dark sky",
                             TONE_FAINT))
        if not moon.get("rise_unix") and not moon.get("set_unix"):
            up = _num(moon.get("alt")) > 0.0
            timed.append(row(
                night["dark_start_unix"] or dusk,
                (f"Moon is up all night, {pct}% illuminated{near}" if up
                 else f"Moon stays down all night ({pct}% illuminated) — "
                      f"no moonglow in any of it"), TONE_FAINT))

    # 7. the meridian — only for targets that actually get a window. A pool
    # candidate that never clears its floor still crosses the meridian, and a
    # FLIP row for a target nobody images is a warning about nothing.
    for t in out["targets"]:
        if t.get("meridian_flip_unix") and t.get("window"):
            timed.append(row(t["meridian_flip_unix"],
                             f"{t['label']} crosses the meridian — engine flips, "
                             f"re-centers via plate solve, restarts guiding; "
                             f"worst case one frame lost", TONE_WARN))

    # 8. the rules, which have no hour. Read off the COMPILED instructions, not
    #    the graph: what fires tonight is what compiled, and a node wired to
    #    nothing compiles to nothing.
    whens = {str(i.get("when") or "") for i in (plan.get("instructions") or [])}
    if "on_clouds_in" in whens or "on_clouds_clear" in whens:
        rules.append(row(None, "IF clouds in → hold the loop · calibration queue "
                               "(black slot → darks → bias → flats-if-panel) · "
                               "clean resume when it clears", TONE_WARN, "ANY"))
    if any(w.startswith("on_") and w not in
           {"on_clouds_in", "on_clouds_clear", "on_unsafe", "on_frame_graded"}
           for w in whens):
        rules.append(row(None, "IF the watchdog condition holds → its wired "
                               "actions fire at the next frame boundary",
                         TONE_WARN, "ANY"))
    if "on_unsafe" in whens:
        rules.append(row(None, "IF unsafe (or a stale reading) → abort, park, "
                               "warm, close — fail closed", TONE_BAD, "ANY"))

    # 9. the budget
    for b in out["budget"]:
        if b["has_ledger"]:
            rules.append(row(
                None,
                f"{b['filter']}: {b['banked_h']:g} h banked / {b['goal_h']:g} h "
                f"goal — tonight adds ≈{b['tonight_h']:g} h; the session ledger "
                f"resumes the remainder next clear night", TONE_GOOD, "BUDGET"))
        else:
            rules.append(row(
                None,
                f"{b['filter']}: {b['goal_h']:g} h goal — tonight adds "
                f"≈{b['tonight_h']:g} h. No session ledger was read, so nothing "
                f"here is counted as already banked", TONE_GOOD, "BUDGET"))

    # 10. dawn. The report clause is only claimed when the GRAPH was supplied —
    # a session-report sink compiles to nothing, so a plan alone cannot know
    # whether the night leaves a ledger, and omitting it beats asserting it.
    closers = ""
    if automation.get("dome"):
        closers += ", dome closes"
    if graph is not None and any(n.type == "report" for n in graph.nodes):
        closers += ", session report appended"
    timed.append(row(dawn, f"Dawn: loop ends, mount parks, camera warms{closers}"))

    timed.sort(key=lambda r: r["t_unix"] if r["t_unix"] is not None else 0.0)
    # ...then the hourless rows, then the dawn line last, which is the
    # prototype's order and reads as the night's closing sentence.
    tail = timed.pop() if timed else None
    return timed + rules + ([tail] if tail else [])


def _target_rows(out: dict) -> list[dict]:
    """The "what gets imaged" sentence(s).

    THREE different things can be true of a target and only one of them is the
    prototype's sentence: it has a window, it is placed and never clears its
    floor, or it could not be placed at all. The third is NOT the second —
    saying "never clears 30°" about a target whose position we do not know
    states a fact about the sky that nobody measured.
    """
    targets = out["targets"]
    if not targets:
        return []
    night = out["night"]
    placed = [t for t in targets if t.get("resolved")]
    unplaced = [t["label"] for t in targets if not t.get("resolved")]
    rises = [t for t in placed if t.get("window")]
    anchor = (min(t["window"]["start_unix"] for t in rises) if rises
              else (night["dark_start_unix"] or night["dusk_unix"]))
    floor = targets[0].get("min_altitude_deg") or DEFAULT_MIN_ALT_DEG
    pooled = any(t.get("pool_rank") for t in targets)
    rows: list[dict] = []

    if placed:
        names = ", ".join(t["label"] for t in placed[:6])
        if not rises:
            rows.append({
                "t_unix": anchor, "label": "",
                "msg": (f"{names} never clears {floor:g}° in the dark tonight — "
                        f"nothing placed in this flow has a window from here"),
                "tone": TONE_WARN})
        elif pooled:
            rows.append({
                "t_unix": anchor, "label": "",
                "msg": (f"Pool re-scores {len(placed)} candidates each cycle "
                        f"(altitude × moon separation × hour angle) — best "
                        f"available wins; re-evaluates on completion or an "
                        f"altitude floor"), "tone": TONE_TEXT})
        else:
            rows.append({
                "t_unix": anchor, "label": "",
                "msg": (f"{names} above {floor:g}° — slew, center, focus, "
                        f"guide, loop"), "tone": TONE_TEXT})
    if unplaced:
        rows.append({
            "t_unix": anchor, "label": "",
            "msg": (f"No coordinates for {', '.join(unplaced)} — not in the "
                    f"catalogue and none typed, so there is no curve for it "
                    f"and the scheduler cannot score it"),
            "tone": TONE_WARN})
    return rows
