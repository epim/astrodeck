# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..sequence.schedule import (_clock_time_near_now, hours_to_meridian_flip,
                                 observing_night, prev_sun_event)
from .compile import (CAMPAIGN_UNTIL, _finite, _grid_of, campaign_block,
                      compile_plan, flow_order, grid_size, is_multi_panel,
                      loop_wires, needs_wire_scoping, owner_of, parse_skip)
from .models import FlowGraph, _not_a_count
from .nodes import NODE_DEFS, dusk_auto_resume, parse_cycle_plan, target_angle
from .rig import RigFacts

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

#: What ``resolve_tonight``'s ``progress`` takes: the flow's progress answer
#: (``progress.flow_progress``'s), a callable returning it, or None.
ProgressSource = (Callable[[], Mapping[str, Any] | None] | Mapping[str, Any]
                  | None)

_NO_SITE = ("No observatory site is set, so there is no night to resolve - "
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
    # OverflowError too (#423), as ``compile._num`` and ``doctor._num`` have
    # caught it since S4: ``float()`` of an integer past a float's range (a
    # raw POST or a hand-edited file can hold a 400-digit one) raises that,
    # not ValueError, and it took the whole Tonight answer down with it.
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _not_finite(value: Any) -> bool:
    """True when ``value`` is a number and not a finite one: an infinity, a
    NaN, or an integer past a float's range (#423).

    ``models._not_a_count`` without its "above 0": a DUSK offset may be
    negative, so only the numbers that are no number of minutes at all are
    judged. None, blank text and text that is not a number are False, as
    they are there: the compile reads each as its default, and the editor
    stores a blank field that way."""
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
    except OverflowError:
        return True


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
    from ..site_gate import site_get, site_is_set
    get = site_get(site)
    if not site_is_set(site):
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


def target_own_window(ra_hours: float, dec_deg: float, *, site: Any,
                      min_altitude_deg: float = 0.0,
                      now: float | None = None,
                      date: str | None = None) -> dict | None:
    """This target's OWN best observing window tonight: astronomical dark
    (-18 deg, ``visibility.ASTRO_DARK_DEG``) AND above ``min_altitude_deg`` —
    exactly ``resolve_tonight``'s per-target ``window`` (the one the Tonight
    card draws), factored out so a caller that is not building the whole
    card can ask it of one target. Returns ``{start_unix, end_unix,
    mean_alt}``, or ``None`` when the site cannot be read or the target
    never clears ``min_altitude_deg`` in the dark tonight.

    THE GAP THIS CLOSES (#596, backlog shape b). A flow's compiled
    ``Target.schedule`` carries the AUTORUN window — dusk (at the RIG's own
    ``twilight_deg``) plus the card's offset, ONE clock shared by every
    target in the plan. This is a different, per-target answer: NGC 7331's
    own window opened on 2026-09-29 63 minutes after the flow's shared autorun
    window (dusk -30 min) — a 63-minute gap, because dusk at
    the rig's configured twilight angle is not the same instant as THIS
    target clearing the true dark sky and its own altitude. The engine's
    ``_setup_target`` asks here, not the compiled schedule, before it spends
    the night's one autofocus and guider calibration on a target that is
    not up yet.

    BEST-EFFORT, LIKE ``schedule.gating_status``'s "no site" branch: a site
    that cannot be read answers None rather than raise, so a caller that
    cannot judge this treats it as nothing to wait for, never as a new way
    to withhold a run."""
    sd, _why = _site_dict(site)
    if sd is None:
        return None
    t_now = time.time() if now is None else float(now)
    d = date or _night_date(t_now, sd["longitude"])
    # Lazy, as ``resolve_tonight`` imports it: astropy and FastAPI should
    # not load just because a module imported ``flows.tonight``.
    from ..catalog.visibility import NoSite, compute_night
    try:
        night = compute_night(ra_hours, dec_deg, date=d,
                              alt_limit=min_altitude_deg, site=sd)
    except NoSite:
        return None
    bw = night.get("best_window")
    if not bw:
        return None
    return {"start_unix": bw["start_unix"], "end_unix": bw["end_unix"],
            "mean_alt": bw["mean_alt"]}


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
    mapping — so the route can supply the impure half (the reports' ledger
    summaries, ``SessionReporter.summaries``, #536) and this module can stay
    callable without one. A summary carries a report's ``by_filter`` and its
    ``targets``, the only two fields read here, so it answers both readings
    below exactly as the report would.

    ``targets``, when given, restricts the sum to reports' per-target
    breakdowns for those names.

    WHOSE HA COUNTS TOWARD THIS FLOW'S HA GOAL: THIS FLOW'S TARGETS' (#536, H4
    orchestrator ruling 6). The route passes ``targets``, the names this
    flow's run records frames under (``flow_target_names``: the compiled
    targets, a mosaic's panels by their panel names), and the row says "for
    these targets". The default, every hour in a filter in the archive
    whatever it was pointed at, filled M31's bar with M16's frames for anyone
    shooting two Ha projects, and since #419 made the route read the reports
    it did: an operator reading "banked vs goal" took the archive's hours
    for the flow's progress. The default stays for a caller that has already
    chosen its reports.
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


def _entry_target_names(entry: dict) -> list[str]:
    """The ledger names ONE compiled target entry's own rows stand for: a
    single target or a pool member its own name, a mosaic its live panels
    (never the bare block name) - the same split ``flow_target_names`` makes
    over a whole plan, factored out so a BUDGET row can ask it of its own
    entry alone (#562: a row must read its own block's bank, not the whole
    flow's)."""
    label = str(entry.get("name") or "").strip()
    grid = _mosaic_grid(entry)
    if grid is None:
        return [label] if label else []
    rows, cols, skip = grid
    return [f"{label} {_panel_label(r, c)}" if label else _panel_label(r, c)
            for r in range(rows) for c in range(cols) if (r, c) not in skip]


def flow_target_names(plan: dict | FlowGraph, name: str = "") -> list[str]:
    """The names this flow's run records its frames under, in plan order: the
    ledger's meaning of "this flow's own targets" (#536, H4 orchestrator
    ruling 6).

    ``plan`` is the graph, compiled here as ``resolve_tonight`` compiles it,
    or a compiled plan. A single target and a pool member are recorded under
    their compiled name. A MULTI-PANEL TARGET IS RECORDED BY PANEL: the run
    names each panel "<name> <row>-<col>", 1-based (``to_plan``'s
    ``_expand_mosaic``), and a report's rows carry those names, so its names
    are its live panels', and never the bare block name, under which only a
    different, single-target flow of the same object records frames. A
    skipped panel is left out as the plan leaves it out, so what is counted
    as banked matches the goal, which is the live panels' (``_budget``). The
    names are the ones ``to_sequence_plan`` gives its targets, which
    tests/test_h4_budget_for_these_targets.py holds this to; a target the
    plan drops for want of coordinates is still named, since it is still
    this flow's.
    """
    plan_dict = (compile_plan(plan, name) if isinstance(plan, FlowGraph)
                 else dict(plan or {}))
    out: list[str] = []
    for entry in plan_dict.get("targets") or []:
        for n in _entry_target_names(entry):
            if n and n not in out:
                out.append(n)
    return out


#: What a BUDGET row's banked figure says it holds (#536): the hours of this
#: flow's own targets (``flow_target_names``), which is what the route counts.
#: Both Tonight surfaces print the server's row as it is, so this is the one
#: place the words live; tonightPanelDom.test.tsx and
#: budgetForTheseTargets.test.tsx read them off the server's answer.
_FOR_THESE = "for these targets"


# --------------------------------------------------------------- target coords

#: The catalogue row kinds whose position is a function of TIME: a planet, the
#: Moon or the Sun (``solar_system``), a comet and a satellite are computed from
#: an ephemeris at the search's ``when``. Every other kind (``dso``, ``star``,
#: ``coordinates``) is a fixed J2000 row. A kind this set does not name reads
#: as fixed, so a new kind added to the catalogue keeps ADOPT's separation
#: bound against tonight's target until someone decides it moves
#: (``continuation.adopt_matches``). A satellite counts as a body (H3
#: orchestrator ruling 9, spec, Still waiting on the owner, item 18): its old
#: pointing is checked against its ephemeris at capture time like a planet's.
MOVING_KINDS = frozenset({"solar_system", "comet", "satellite"})

#: THE ONE INSTANT A NAME'S IDENTITY IS ASKED AT (#249): 2026-09-01T00:00:00
#: UTC. ``catalog.objects.search`` ranks its hits by (rank, magnitude, id), and
#: a body's magnitude is computed for the search's ``when``, so when a body and
#: a fixed row share a name's best rank, WHICH one comes first can change
#: between two instants. Asked at each caller's own instant, ``to_plan`` (the
#: compile's), ``progress._single`` (now) and ADOPT could name one TARGET two
#: objects: the card then read "nothing banked" against a live ledger, and two
#: nights' compiles minted two sets of step ids. Asked here, every caller gets
#: one answer, whatever its clock. The row's COORDINATES are still taken at
#: the caller's ``when`` (``resolve_target``).
#:
#: A NAMED INSTANT, NOT A TIME-FREE TIE-BREAK. A tie-break inside a rank needs
#: the rank, which the search does not return; building it again here would be
#: a second copy of the catalogue's ranking. What a fixed instant must still
#: do is place every kind of row, and a satellite is the one that stops: SGP4
#: refuses an element set far from its epoch. Measured on the ISS element set
#: ``tests/test_satellite_ephemeris.py`` pins (epoch 2026-09-10), SGP4
#: propagates it about 5,870 days back and 3,680 days forward; at J2000 it
#: refuses, and "ISS" then resolves to the star Meissa. So the instant sits
#: at the element sets current when it was chosen, where that set places the
#: ISS and 2P/Encke (``tests/test_flows_identity_one_when.py``), and
#: ``_identity_row`` falls back to the caller's instant for a moving row this
#: one cannot place, rather than let a name slide onto whatever else it
#: matches. The one input left that can move a name's identity is the
#: catalogue itself: an element-set refresh, or a site that makes satellites
#: placeable at all.
IDENTITY_WHEN = 1_788_220_800.0

#: How many rows each of ``resolve_target``'s two searches keeps. The row a
#: name is has the best rank at either instant, so at the caller's instant
#: only rows of that rank can sort ahead of it: a body or a comet whose
#: magnitude moved (satellites carry none), or, when the row is itself one,
#: the rows its own magnitude fell behind. 100 is room for every body and
#: comet the catalogue places beside it (comets are capped at 25 a search);
#: a name whose best rank a hundred rows share is no TARGET's name, and
#: there the row is no answer rather than a guess.
_RESOLVE_ROWS = 100


@dataclass(frozen=True)
class NameResolution:
    """What the shipped catalogue says a target NAME is.

    ``identity`` is the row's catalogue id, which is canonical: every spelling
    that finds the row finds the same id ("M 31", "m31" and "Andromeda" are
    all "M31"), and for a moving body the id is its canonical name ("Jupiter",
    however it was typed). ``moves`` says whether the row's position is a
    function of time (``MOVING_KINDS``)."""
    ra_hours: float
    dec_deg: float
    identity: str
    moves: bool


def _row_key(row: dict) -> tuple:
    """What makes two search rows one object: its kind, its trimmed id and,
    for a satellite, its catalogue number (debris pieces share a name)."""
    return (row.get("kind"), str(row.get("id") or "").strip(),
            row.get("norad_id"))


def _identity_row(at_when: list[dict], at_identity: list[dict]
                  ) -> dict | None:
    """The row a name IS, as it stands at the caller's instant, or None.

    ``at_identity`` is the search at ``IDENTITY_WHEN`` and decides which row;
    ``at_when`` is the search at the caller's ``when`` and supplies that row's
    coordinates. None when the chosen row cannot be placed at ``when`` (a
    body whose ephemeris failed there): the name's first hit at ``when`` is
    then another object, and pointing at it would be the slide this exists
    to stop.

    A MOVING ROW THE SHARED INSTANT CANNOT PLACE IS DECIDED AT ``when``: a
    satellite past its element set's SGP4 horizon is missing from
    ``at_identity``, and the fixed instant's first hit is then something else
    the name happens to match (``IDENTITY_WHEN``)."""
    if not at_when:
        return None
    first = at_when[0]
    if not at_identity or (
            first.get("kind") in MOVING_KINDS
            and _row_key(first) not in {_row_key(r) for r in at_identity}):
        return first
    want = _row_key(at_identity[0])
    return next((r for r in at_when if _row_key(r) == want), None)


def resolve_target(name: str, when: float | None = None
                   ) -> NameResolution | None:
    """Where, which and whether it moves, for a target NAME, from the shipped
    catalogue; None when the catalogue has no row.

    THE ONE RESOLVER (#229). ``to_plan`` points a name-only TARGET with its
    coordinates and keys its ids on its ``identity``; ``progress._single``
    finds those ids again through the same ``identity``; ADOPT learns from
    ``moves`` whether a name is a body, and where that body was at the
    instants its frames were taken. Three callers asking one function is
    what keeps "which object is this name" from being answered two ways, as
    "M 31" and "M31" were when the key was the name as typed.

    WHICH ROW AT ONE INSTANT, WHERE AT ``when`` (#249). The row is the
    catalogue's first hit at ``IDENTITY_WHEN``, whatever ``when`` the caller
    passes, so the compile, the card and ADOPT name one object; its
    coordinates are that row's at ``when``, so a body is still pointed where
    it is at the compile. ``_identity_row`` has the two exceptions.

    A row without an id cannot be keyed, so it is no answer: every kind the
    catalogue ships carries one (``catalog.objects.search``).

    Late-bound by every caller (``tonight.resolve_target``, looked up at call
    time), so a test that replaces it here replaces it for all three."""
    try:
        from ..catalog.objects import search
        at_when = search(str(name), limit=_RESOLVE_ROWS, when=when).rows
        at_identity = at_when if when == IDENTITY_WHEN else search(
            str(name), limit=_RESOLVE_ROWS, when=IDENTITY_WHEN).rows
    except Exception:       # noqa: BLE001 - a missing/broken catalogue means we
        return None         # do not know where this is, not a 500 for the panel
    row = _identity_row(at_when, at_identity)
    if row is None:
        return None
    try:
        ra_hours, dec_deg = float(row["ra_hours"]), float(row["dec_deg"])
        identity = str(row["id"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not identity:
        return None
    return NameResolution(ra_hours=ra_hours, dec_deg=dec_deg,
                          identity=identity,
                          moves=row.get("kind") in MOVING_KINDS)


def catalog_coords(name: str, when: float | None = None
                   ) -> tuple[float, float] | None:
    """``(ra_hours, dec_deg)`` for a target NAME, from the shipped catalogue.

    A TARGET POOL compiles to names and constraints and NOTHING ELSE — the
    prototype's pool members are "M16, M17, M8, NGC 6946" — so without a lookup
    the best-of-four example has four targets and not one altitude curve. The
    catalogue is local, static data, so this stays deterministic; it is
    imported lazily and injectable (``resolve_name``) so a caller without the
    catalogue, or a test, is not forced through it. The coordinates of
    ``resolve_target``'s answer, so the panel and the run place a name alike.
    """
    hit = resolve_target(name, when)
    return None if hit is None else (hit.ra_hours, hit.dec_deg)


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

def _dusk_flats_wired() -> bool:
    """Whether the engine runs a DUSK FLATS block: ``to_plan.DUSK_FLATS_WIRED``,
    the one switch (#192, #603).

    The brief's clause and the STORY row are worded from this, and the
    compiler's "will not take flats" warning is present exactly when it is
    False, so the preview cannot promise what the plan's own warning denies.
    Read AT CALL TIME and imported lazily: ``to_plan`` imports this module, so
    a top-level import of its constant would bind the value at load (a test
    or a flip of the constant would not reach the copy) and could cycle."""
    from . import to_plan
    return bool(to_plan.DUSK_FLATS_WIRED)


#: Said in place of what the block would do while the engine has no dusk-flats
#: stage. Operator-entered text and fixed words only: the Tonight answer is
#: served to roles with no site view, so no figure the site decides (the
#: window's length in minutes is a function of the latitude, #19) may enter it.
_FLATS_NOT_RUN = "the engine has no dusk-flats stage yet"


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
                    banked_by_target: Callable[
                        [], Mapping[str, Mapping[str, float]]] | None = None,
                    frames_by_target: Callable[
                        [], Mapping[str, Mapping[str, int]]] | None = None,
                    resolve_name: Callable[[str], tuple[float, float] | None] | None = None,
                    step_min: int = CURVE_STEP_MIN,
                    hop_cost_s: float | None = None,
                    progress: ProgressSource = None,
                    rig: RigFacts | None = None) -> dict:
    """Everything the Tonight panel draws, for one flow, at one instant.

    ``plan`` is a compiled plan (``compile.compile_plan``) or the graph itself,
    which is compiled here — because the README says the timeline is a rendering
    of the compile, and a caller that could hand this function a graph WITHOUT
    compiling it would be able to render a night the run would not run.

    ``banked`` is the session ledger, injected: hours already in the bank per
    filter, FOR THIS FLOW'S TARGETS, since the BUDGET row says so (#536; the
    route folds the ledger with ``targets=flow_target_names(...)``). It is a
    callable and not a mapping so the route can read ``captures/reports``
    lazily and this function can stay pure; the default is NO LEDGER, and a
    budget row then says the goal and tonight's contribution and explicitly
    does not claim a banked figure.

    ``banked_by_target`` is the same ledger, kept by target name rather than
    folded into one flow-wide mapping (#562): ``{target: {filter: hours}}``,
    which lets each BUDGET row read only the hours of the entries it stands
    for (``_budget``'s ``_entry_target_names``), so a block that shares a
    filter with another block in the same flow is not given the other's
    hours. When given, it is preferred over ``banked`` for the figure every
    row actually prints; ``banked`` alone still answers ``has_ledger`` and is
    what a caller with only the coarse, flow-wide figure can supply.

    ``hop_cost_s`` is the MEASURED mean cost of one hop between targets, in
    seconds (``SequenceEngine.measured_cost("hop")``'s mean, #189 S3), or
    None while nothing has measured one. Only a mosaic reads it: its budget
    rows add the hops (``_budget``) and its brief sentence names the cost
    (``brief``). None is never replaced by the engine's 150 s seed: a seed is
    a guess, and the brief says "not measured yet" instead.

    ``rig`` is the route's one reading of the rig (``flows.rig.RigFacts``).
    Only the brief reads it, for the guider the run will guide with and its
    settle and dither (#506); None, as a test or a preview hands over, is a
    rig nobody described, and the brief then names Rig > Guider as their
    source rather than a number.

    ``progress`` is the flow's progress answer, ``progress.flow_progress``'s,
    or a callable returning it (read lazily, and only for a flow with a
    mosaic). The CAMPAIGN tab's per-panel rows come from it (``_campaign``),
    with no pool required. None, or a callable that raises, is no progress:
    the rows are left out and the note says so.

    A MULTI-PANEL TARGET IS ONE BLOCK (spec 3.2, #189 U-10). Its compiled
    entry is one entry, so it gets ONE ``compute_night``, on its centre, like
    any target: one curve, one window, one flip. Its row adds ``mosaic``
    (``_mosaic_night``): each shot panel's peak altitude, stamped by
    ``framing._stamp_transit_alt`` on the panel centres ``compute_mosaic``
    lays out, at ``site`` (#336), and the ``band`` the timeline draws from
    the worst and the best of them. Single targets and pool members carry
    no such key, so their answer is what it was.

    Returns ``{ok, reason, now_unix, twilight_deg, night, flats, moon, targets,
    budget, resume_across_nights, campaign, brief, story}``; on failure ``ok``
    is False, ``reason`` says why in a sentence, and every time-bearing key is
    None/empty rather than plausible.
    """
    t_now = time.time() if now is None else float(now)
    graph = plan if isinstance(plan, FlowGraph) else None
    plan_dict: dict = compile_plan(graph, name) if graph is not None else dict(plan or {})
    rig_tw = _twilight(twilight_deg)
    sched = plan_dict.get("schedule") or {}
    # THE WINDOW IT RESOLVES (backlog WP-09, #191): a DUSK WINDOW's
    # Astro/Nautical/Civil dusk Start compiles its own ``twilight_deg``
    # (`compile._dusk_schedule`), and this preview reads it here, before the
    # sun-crossing search, so a card that chose astro dusk shows astro
    # dusk's own instant and says "sun -18°" rather than the rig's one
    # setting repeated under three different labels. Absent (no schedule, a
    # "Clock time" Start, or a flow this build does not recognise), ``tw``
    # is the rig's angle, unchanged.
    target_tw = sched.get("twilight_deg")
    tw = rig_tw if target_tw is None else float(target_tw)
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

    automation = plan_dict.get("automation") or {}
    offset_min = _num(sched.get("start_offset_min"))
    start_mode = str(sched.get("start_mode") or "now")
    stop_mode = str(sched.get("stop_mode") or "")
    min_alt = _num(sched.get("min_altitude_deg"), DEFAULT_MIN_ALT_DEG)

    # The autorun window, as the ENGINE will resolve it (#191): dusk plus the
    # node's offset for a sun-based start; the nearest occurrence of the
    # card's own clock time for "Clock time" (``schedule._clock_time_near_now``,
    # the same reading ``resolve_window`` gives that mode); ``now`` for
    # anything else, as a flow with no window runs. BEFORE THIS a "Clock
    # time" start drew the timeline from ``now``, same as no window at all,
    # although the compiled schedule already carried a real start time.
    if start_mode == "dusk":
        window_start = dusk + offset_min * 60.0
    elif start_mode == "time":
        window_start = _clock_time_near_now(sched.get("start_time"), t_now) or t_now
    else:
        window_start = t_now
    # Closing at dawn for "dawn", at the card's own clock time for "Clock
    # time" (None when it does not parse - unresolvable, as a boundary
    # already reads elsewhere, #527), and not at all for "none" - which
    # compile_plan now warns about instead of leaving the operator to find
    # out at dawn (#191).
    if stop_mode == "dawn":
        window_stop = dawn
    elif stop_mode == "time":
        window_stop = _clock_time_near_now(sched.get("stop_time"), t_now)
    else:
        window_stop = None

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
                **_mosaic_key(entry, None, date, site=sd),
            })
            continue
        ra_h, dec_d = coords
        # ONE PER BLOCK, a mosaic's included (spec 3.2): the block's centre
        # gives its curve, window and flip. Its panels' spread is the peak
        # altitude `_mosaic_key` stamps on each, which is a far cheaper pass
        # than a night per panel and is the number the modal already shows.
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
            **_mosaic_key(entry, coords, date, site=sd),
        })

    flats = None
    if automation.get("dusk_flats"):
        df = automation["dusk_flats"]
        f0, f1 = _flats_window(str(df.get("window") or ""), lat, lon, dusk)
        flats = {"start_unix": f0, "end_unix": f1,
                 "window": df.get("window"), "adu_target": df.get("adu_target"),
                 "count": df.get("count"), "method": df.get("method")}

    budget = _budget(plan_dict, banked, hop_cost_s, banked_by_target)

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
        # WHETHER A SUBSEQUENT NIGHT RESUMES THIS FLOW (#712): the compiled
        # plan's own flag, which `compile_plan` writes False only for an
        # explicit DUSK WINDOW Automatic resume Off, so absent reads True, as
        # `_story`'s budget rows read it. The Target modal's campaign line
        # (`framingApi.campaignLine`) has nothing but this answer to read the
        # plan from, and said "resumes at the next dusk" of an Off flow. A
        # plain boolean: no figure the site decides.
        "resume_across_nights": plan_dict.get("resume_across_nights") is not False,
        # The CAMPAIGN tab and the STORY tab's brief. Both read the GRAPH, not
        # the compile, so both are empty when a caller hands in a plan dict -
        # the same rule the dawn story already follows for the report sink.
        "campaign": _campaign(graph, frames_by_target, progress),
        # The brief is handed this compile, so the visit it states is read
        # off the same entry the budget prices its hops from, and the rig
        # facts, so the guider it names is the one the run will use (#506).
        "brief": brief(graph, hop_cost_s=hop_cost_s, plan=plan_dict,
                       rig=rig),
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


# --------------------------------------------------------------------- mosaics
#
# Spec 3.2, S3 item 5, #189 U-10. A multi-panel TARGET compiles to ONE entry,
# and Tonight treats it as one block: one night on its centre, and on top of
# that each shot panel's peak altitude and the band the timeline draws from
# the worst and the best of them.

def _mosaic_grid(entry: dict) -> tuple[int, int, set[tuple[int, int]]] | None:
    """``(rows, cols, skipped)`` for a compiled mosaic entry, the skipped
    panels as 0-based ``(row, col)`` cells, or None for any other entry.

    The compile writes ``skip`` 1-based and only with cells of its own grid
    (``compile.parse_skip``); a cell outside the grid, which only a compiled
    dict built by hand could hold, skips nothing, as ``to_plan`` skips
    nothing for it."""
    m = entry.get("mosaic")
    if not m or entry.get("pool_rank") is not None:
        return None
    rows = max(1, int(_num(m.get("rows"), 1)))
    cols = max(1, int(_num(m.get("cols"), 1)))
    skip: set[tuple[int, int]] = set()
    for cell in m.get("skip") or []:
        try:
            r, c = int(cell[0]) - 1, int(cell[1]) - 1
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= r < rows and 0 <= c < cols:
            skip.add((r, c))
    return rows, cols, skip


def _live_panels(entry: dict) -> int | None:
    """How many panels a mosaic entry shoots (its grid less its skips), or
    None for an entry that is not a mosaic."""
    grid = _mosaic_grid(entry)
    return None if grid is None else grid[0] * grid[1] - len(grid[2])


def _panel_label(row: int, col: int) -> str:
    """A panel as the operator names it: 1-based "row-col", the suffix
    ``to_plan`` gives each panel's target, so "2-1" here is the row the
    Plan and the log call "M31 2-1"."""
    return f"{row + 1}-{col + 1}"


def _run_sync(coro: Any) -> Any:
    """Run a coroutine to its end from this synchronous module.

    ``framing._stamp_transit_alt`` is async (the mosaic route awaits it), and
    this resolver runs on a worker thread (the route's ``to_thread``), where
    no loop is running, so ``asyncio.run`` serves. A caller already inside a
    running loop cannot use ``asyncio.run``; the coroutine then gets a loop
    of its own on a one-off thread, rather than a RuntimeError out of the
    Tonight panel."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _mosaic_key(entry: dict, coords: tuple[float, float] | None,
                date: str, *, site: dict) -> dict:
    """``{"mosaic": ...}`` for a mosaic's Tonight row, ``{}`` for every other
    row, so a single target's and a pool member's rows keep their keys."""
    night = _mosaic_night(entry, coords, date, site=site)
    return {} if night is None else {"mosaic": night}


def _mosaic_night(entry: dict, coords: tuple[float, float] | None,
                  date: str, *, site: dict) -> dict | None:
    """A mosaic block's panels tonight, or None for an entry that is not one::

        {rows, cols, live, skipped: ["2-1", ...],
         panels: [{panel, row, col, transit_alt | transit_alt_error}],
         band: null | {worst: {panel, row, col, transit_alt}, best: {...}},
         [reason]}

    THE PANELS ARE THE RUN'S. They are laid out by ``framing.compute_mosaic``
    from the block's centre, grid, overlap, field and layout angle, the one
    projection ``to_plan`` expands the block with (spec 3.3: "there is no
    third copy"), read through ``to_plan``'s own ``_angles`` and
    ``_mosaic_numbers``, so the panels here are the panels the run slews to.
    A skipped panel is not shot, so it is listed in ``skipped`` and not
    stamped.

    THE PEAK ALTITUDES ARE ``framing._stamp_transit_alt``'s, the per-panel
    stamp the mosaic route and the modal's altitude column use, run on the
    panel centres for the night this resolver pinned (``date``). A panel it
    could not answer for carries its ``transit_alt_error``, the same words the
    modal shows, never a gap.

    AT ``site``, THE ONE THE RESOLVER WAS HANDED (#336). The stamp used to
    take no site, so it read the hub's: the curves were the caller's site
    and the panels the configured one, and a script that resolved a night
    for a synthetic site printed panel altitudes at the real one, which is a
    latitude oracle (#140). ``site`` is keyword-only and has no default, so
    no caller of this helper reaches the hub's site by leaving it out. The
    mosaic route still hands the stamp nothing, because the hub's site is
    the one that route is about.

    THE BAND is the reduction ``mosaicNightSummary`` makes in the UI: the
    lowest and the highest peak among the panels answered, so the timeline
    can draw the spread the centre's one curve cannot show. Ties go to the
    panel first in grid order. None when no panel is answered.

    NOTHING PLACED IS NOT GUESSED. With no coordinates, no camera field (M1),
    no angle (M2) or a grid ``to_plan`` would refuse, the panels cannot be
    laid out, so ``panels`` is empty, ``band`` is None and ``reason`` says
    why. The run refuses the same graph; Tonight still draws its centre."""
    grid = _mosaic_grid(entry)
    if grid is None:
        return None
    rows, cols, skip = grid
    out: dict = {"rows": rows, "cols": cols,
                 "live": rows * cols - len(skip),
                 "skipped": [_panel_label(r, c) for r, c in sorted(skip)],
                 "panels": [], "band": None}
    why = ""
    if coords is None:
        why = "the block has no coordinates, so no panel can be placed"
    else:
        # Lazy, both: `catalog.framing` is the Atlas router and loads the auth
        # layer, and `to_plan` imports this module, so neither is a top-level
        # import here (`to_plan._expand_mosaic` imports framing the same way).
        from ..catalog import framing
        from .to_plan import GraphNotRunnable, _angles, _mosaic_numbers
        try:
            nums = _mosaic_numbers(entry)
        except GraphNotRunnable as e:
            nums, why = None, str(e)
        layout = _angles(entry)[1]
        if nums is not None and layout is None:
            why = ("the block has no camera angle, and a grid is laid out at "
                   "one, so its panels cannot be placed")
        elif nums is not None and not (
                (nums["fov_x"] or 0) > 0 and (nums["fov_y"] or 0) > 0):
            why = ("the block has not recorded a camera field, so its panels "
                   "cannot be placed")
        elif nums is not None:
            spec = {"ra_hours": coords[0], "dec_deg": coords[1],
                    "rows": nums["rows"], "cols": nums["cols"],
                    "overlap": nums["overlap"] / 100.0,
                    "rotation_deg": layout, "fov_x_deg": nums["fov_x"],
                    "fov_y_deg": nums["fov_y"]}
            panels = [{"row": p["row"], "col": p["col"],
                       "ra_hours": p["ra_hours"], "dec_deg": p["dec_deg"]}
                      for p in framing.compute_mosaic(spec)["panels"]
                      if (p["row"], p["col"]) not in skip]
            _run_sync(framing._stamp_transit_alt(panels, date, site=site))
            panels.sort(key=lambda p: (p["row"], p["col"]))
            out["panels"] = [
                {"panel": _panel_label(p["row"], p["col"]),
                 "row": p["row"], "col": p["col"],
                 **{k: p[k] for k in ("transit_alt", "transit_alt_error")
                    if k in p}}
                for p in panels]
            out["band"] = _band(out["panels"])
    if why:
        out["reason"] = why
    return out


def _band(panels: list[dict]) -> dict | None:
    """``{worst, best}``: the panels with the lowest and the highest peak
    altitude among those answered, first in grid order on a tie (``panels``
    arrives in grid order), or None when none is answered."""
    answered = [p for p in panels if "transit_alt" in p]
    if not answered:
        return None

    def ref(p: dict) -> dict:
        return {k: p[k] for k in ("panel", "row", "col", "transit_alt")}

    worst = min(answered, key=lambda p: p["transit_alt"])
    best = max(answered, key=lambda p: p["transit_alt"])
    return {"worst": ref(worst), "best": ref(best)}


# ---------------------------------------------------------------- the ledger row

def _measured(hop_cost_s: Any) -> float | None:
    """A hop cost worth pricing with: a finite number of seconds above 0, or
    None. Anything else is "not measured", never a zero-cost hop."""
    if isinstance(hop_cost_s, bool) or not isinstance(hop_cost_s, (int, float)):
        return None
    return float(hop_cost_s) if math.isfinite(hop_cost_s) and hop_cost_s > 0 \
        else None


def _count(value: Any, default: int = 1) -> int:
    """A whole count of at least 1 from a compiled value, or ``default``.

    The compile reads every count finite-only (#328), but a mosaic's
    ``passes`` is read through ``_num`` and can arrive as an infinity, which
    ``int()`` raises on: ``to_plan`` refuses such a block, and Tonight should
    still draw it rather than answer 500 (#362's class)."""
    number = _num(value, default)
    if not math.isfinite(number):
        return default
    return max(1, int(number) or default)


def _shown(value: Any) -> str:
    """A stored count as a sentence quotes it: text in quotes, a float as
    Python writes it ("inf", "nan"), and an integer too long to read
    described rather than printed, since a 400-digit quota printed in full
    would be most of the note."""
    if isinstance(value, str):
        text = repr(value)
        return text if len(text) <= 24 else text[:20] + "...'"
    if isinstance(value, float):
        return format(value, "g")
    if isinstance(value, int) and not isinstance(value, bool) \
            and abs(value) >= 10 ** 15:
        return "a number of more than 15 digits"
    return str(value)


def _stored_count(node, key: str, default: int) -> tuple[int | None,
                                                          str | None]:
    """A count as the CAMPAIGN tab reads it off a stored node, a POOL's
    ``quota`` or a FILTER CYCLE's ``perCycle``: ``(count, None)``, or
    ``(None, why)`` for a value that is a number but no finite count above 0
    (#362 item 4, the #328 class).

    ``int()`` of the raw param raised on exactly those: ``"inf"`` and a JSON
    ``1e999`` read as an infinity, whose ``int()`` is ``OverflowError:
    cannot convert float infinity to integer``, and a 400-digit JSON integer
    raised ``OverflowError: int too large to convert to float`` from
    ``_num``'s ``float()`` before ``int()`` was reached. ``_campaign`` let
    either out and took the whole Tonight answer down. Validation refuses
    them at the save (``models.COUNT_PARAMS``), so a new save cannot store
    one, but a flow saved before #328 or a file edited by hand still can,
    and Tonight reads stored flows.

    REFUSED IN WORDS, NOT CLAMPED. There is no count to clamp to: an
    infinite quota is no number of cycles, and the default 45 would draw
    "12/45 cycles" against a quota nobody set. The value is judged by
    validation's own predicate (``models._not_a_count``), so this tab
    refuses exactly the counts a save refuses, 0 and negatives among them,
    which ``max(1, ...)`` used to read as 1. Text that is no number at all
    is read as ``default``, as it always was, because validation does not
    judge it either."""
    value = (node.params or {}).get(key)
    if _not_a_count(value):
        label = NODE_DEFS[node.type].label
        return None, (f"{label} {node.id!r} holds {_shown(value)} as its "
                      f"{key}, and a count must be a finite number above 0, "
                      f"so no member's cycles are counted against it.")
    return max(1, int(_num(value, default))), None


def _per_panel_s(step: dict) -> float:
    """One panel's shutter seconds for a compiled step: a capture's exposure
    times its count, a FILTER CYCLE's slot exposures times its cycles and
    subs per pass (the counts ``to_plan._cycle_steps`` gives each slot)."""
    if step.get("strategy") == "cycle":
        per = max(1, int(_num(step.get("cycles"), 1))) * max(
            1, int(_num(step.get("per_cycle"), 1)))
        return sum(_num(s.get("exposure_s")) for s in step.get("slots") or []) \
            * per
    return _num(step.get("exposure_s")) * _num(step.get("count"))


def _pass_s(entry: dict) -> float:
    """One pass's shutter seconds on one panel: every step's exposure times
    the subs it takes a pass (a cycle's slots at ``per_cycle`` each, a
    capture at its ``per_visit``, 1 where it has none).

    The pass ``readouts._block`` prices (``pass_s``, the sum of each plan
    step's exposure times its ``per_visit``), read off the compiled entry
    instead of the plan: 4 x 60 + 3 x 180 = 780 s on the default cycle
    (spec 5.5)."""
    total = 0.0
    for step in entry.get("steps") or []:
        if step.get("strategy") == "cycle":
            total += sum(_num(s.get("exposure_s"))
                         for s in step.get("slots") or []) \
                * _count(step.get("per_cycle"))
        else:
            total += _num(step.get("exposure_s")) * _count(step.get("per_visit"))
    return total


def _visit_passes(entry: dict) -> int:
    """The passes one visit to a panel makes: ``max(passes, ceil(minVisit /
    pass))`` (spec 5.3), through ``readouts.visit_passes``, the one copy of
    that bound. The Target modal's RUN section prints the same number
    (``readouts``), the brief states it (``_mosaic_sentences``) and the
    budget counts the hops by it (``_visits_per_panel``), so the three
    cannot describe three different visits.

    Imported here, not at the top: ``readouts`` imports the engine and
    ``to_plan``, and ``to_plan`` imports this module."""
    from .readouts import visit_passes
    mosaic = entry.get("mosaic") or {}
    return visit_passes(_count(mosaic.get("passes")),
                        _num(mosaic.get("visit_min")) * 60.0, _pass_s(entry))


def _visits_per_panel(entry: dict) -> int:
    """How many visits, each one hop, one panel of a mosaic takes to shoot
    its whole quota.

    The engine's own rule for the visits a member owes (``SequenceEngine.
    _visits_owed``), with nothing banked: panel-first (no loop wire) is one
    visit; rotating, a panel owes as many rounds as its most-served step
    needs, ``count / per_visit`` rounded up (a cycle's slot owes ``cycles``,
    a capture inside the loop its ``count``), and a visit makes
    ``_visit_passes`` of them.

    THE MINIMUM VISIT IS COUNTED (spec S3 item 5, #353). The engine's
    ``_visits_owed`` leaves ``visit_min_s`` out and so prices the most hops
    a block can make, which is its concern, not this row's. A visit ends at
    a round boundary once it has made its passes AND lasted its minimum, so
    ``minVisit`` 30 on a 13-minute pass makes three rounds a visit, and the
    hops are a third of the passes-alone figure. The brief says a visit is
    that long, and a budget that priced three times the hops would contradict
    the sentence beside it."""
    if entry.get("loop") is not True:
        return 1
    rounds = 0
    for step in entry.get("steps") or []:
        if step.get("strategy") == "cycle":
            owed = max(1, int(_num(step.get("cycles"), 1)))
        else:
            count = max(0, int(_num(step.get("count"))))
            per = max(1, int(_num(step.get("per_visit"), 1) or 1))
            owed = -(-count // per)
        rounds = max(rounds, owed)
    return max(1, -(-rounds // _visit_passes(entry)))


def _drawn(step: dict) -> bool:
    """Whether a compiled step gets a budget row: a capture with an
    integration goal, and every FILTER CYCLE with a filter to shoot (S4
    orchestrator ruling 5, #338). A capture's goal of 0 means "no goal", the
    compile's own rule; a cycle with no slot shoots nothing (``to_plan``
    refuses it), so it has nothing to count."""
    if step.get("strategy") == "cycle":
        return bool(step.get("slots"))
    return _num(step.get("integration_goal_h")) > 0.0


def _slot_filters(step: dict) -> list[str]:
    """A cycle's filters, each once, in slot order."""
    out: list[str] = []
    for slot in step.get("slots") or []:
        name = str(slot.get("filter") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _shares(total_h: float, weights: list[float]) -> list[float]:
    """``total_h`` split in proportion to ``weights``, in hundredths of an
    hour, so the parts ADD UP to the total as it is printed.

    Each part rounded on its own can lose a hundredth: three equal shares of
    1.00 h are 0.33 each and sum to 0.99, a hop the Tonight panel then
    prices on no row. So the whole is rounded once, each part is floored,
    and the hundredths left over go to the parts with the largest
    remainders, the first in row order on a tie. No weight at all (no
    shutter time among the rows) splits evenly."""
    n = len(weights)
    whole = sum(weights)
    parts = [w / whole for w in weights] if whole > 0 else [1.0 / n] * n
    cents = round(total_h * 100)
    raw = [cents * p for p in parts]
    out = [math.floor(r) for r in raw]
    order = sorted(range(n), key=lambda i: (-(raw[i] - out[i]), i))
    for i in order[:max(0, cents - sum(out))]:
        out[i] += 1
    return [round(c / 100.0, 2) for c in out]


def _budget(plan: dict, banked: Callable[[], Mapping[str, float]] | None,
            hop_cost_s: float | None = None,
            banked_by_target:
                Callable[[], Mapping[str, Mapping[str, float]]] | None = None,
            ) -> list[dict]:
    """Banked-vs-goal integration per filter, plus what tonight adds.

    ONE ROW PER DISTINCT STEP, not per target. ``compile_plan`` copies every
    capture step onto every target, so a pool of four candidates carries four
    copies of one Ha loop — and only ONE of them is shot on any given night
    (that is what "best available" means). Summing per target would promise
    four times the integration the rig can deliver, which is the direction of
    error that costs a project a week.

    A FILTER CYCLE HAS A ROW (S4 orchestrator ruling 5, #338). Only a
    CAPTURE carries an integration goal, and rows were made only for goals,
    so a cycle, the lane most mosaics are built on and the eighth Example's,
    was counted nowhere: that Example answered ``budget: []`` for 16 h of
    shutter and 5.33 h of hops. A cycle's row is its shutter time, the slot
    exposures times ``cycles`` times ``per_cycle`` (``_per_panel_s``), in
    ``tonight_h``; its ``goal_h`` is None, because a FILTER CYCLE sets no
    goal, and the story says so in words rather than print "0 h goal". It
    names its filters in ``filter`` ("L, R, G, B"), and adds ``strategy``
    ("cycle") and ``cycles``; its ``banked_h`` is what the ledger holds in
    those filters. A pool's copies of one cycle are deduped as a capture's
    are.

    A MOSAIC IS THE OPPOSITE CASE (spec S3 item 5, #189 U-10). Its one
    compiled entry stands for every panel it shoots, and EVERY panel owes the
    step, each its own quota (spec 1.3 item 3), so its row is ``live`` panels
    times the per-panel figure: ``goal_h`` and ``tonight_h`` both, since the
    goal was set on a step every panel carries. Deduped like a pool, a 3x2
    would promise one panel's hours for six panels' work, the same error in
    the other direction. A mosaic's rows are its own: the scoping rule gives
    its stages to it alone (spec 1.5), so they are neither deduped against
    another entry's nor used to dedupe one. They add two keys:

    * ``panels``, the live count the figures are multiplied by;
    * ``hop_h``, the time spent moving between panels, when a MEASURED hop
      cost is passed, else None. The block's hops are its panels times the
      visits each takes (``_visits_per_panel``), priced at the measured
      cost; a visit shoots every step of the block, so the hops are shared
      among the block's ROWS by their shutter time (``_shares``), and the
      rows add up to the block's whole hop figure. A step with no row (a
      capture with no goal) is given no share: a share on no row is a hop
      the panel prices nowhere, which is what #338 found a cycle's share
      to be. ``tonight_h`` stays shutter time alone: "tonight adds 2 h of
      Ha" is a claim about integration.

    Single targets and pool members keep exactly the capture rows they had.

    WHOSE HOURS A ROW BANKS (#562). ``banked`` answers ONE ``{filter: hours}``
    mapping for the WHOLE flow, so every row that shares a filter with
    another block in the same flow read the flow's total in it, not its own
    block's: an M16 Ha row and an M31 Ha row in the same flow both filled
    with M16's-plus-M31's Ha, so a block that had banked nothing still read
    as progressing on the other block's frames. ``banked_by_target`` is the
    fix: ``{target: {filter: hours}}``, which lets this function look up only
    the names ITS OWN entry stands for (``_entry_target_names``) - a single
    target or pool member its own name, a mosaic its live panels - rather
    than fold the whole flow into one number first. A pool's copies of one
    step are deduped by signature as before, and the deduped row's names are
    the UNION of every pool member that carries that signature, since "best
    available" means any one of them could be the one shot tonight. When
    only ``banked`` is given (a caller with just the flow-wide figure, or a
    flow of a single block where the distinction is moot), the same flat
    number is read for every row, exactly as before.
    """
    hop_s = _measured(hop_cost_s)
    have_flat = banked is not None
    have_by_target = banked_by_target is not None
    flat_bank: Mapping[str, float] = {}
    by_target_bank: Mapping[str, Mapping[str, float]] = {}
    if banked is not None:
        try:
            flat_bank = banked() or {}
        except (OSError, ValueError):
            # A ledger on disk that cannot be read is a missing ledger, not a
            # blank timeline. Say "no ledger" rather than "0 h banked" — those
            # are different claims and only one of them is true.
            flat_bank, have_flat = {}, False
    if banked_by_target is not None:
        try:
            by_target_bank = banked_by_target() or {}
        except (OSError, ValueError):
            by_target_bank, have_by_target = {}, False
    have_ledger = have_flat or have_by_target

    def banked_hours(names: list[str], filters: list[str]) -> float:
        if have_by_target:
            return round(sum(_num(by_target_bank.get(n, {}).get(f))
                             for n in names for f in filters), 2)
        return round(sum(_num(flat_bank.get(f)) for f in filters), 2)

    seen: dict[tuple, int] = {}
    rows: list[dict] = []
    row_names: list[list[str]] = []
    row_filters: list[list[str]] = []
    for target in plan.get("targets") or []:
        live = _live_panels(target)
        if live == 0:
            # Every panel skipped: `to_plan` leaves the block out of the plan,
            # so it adds nothing tonight, and a "0 h goal" row would read as
            # an unmeetable goal rather than an absent one.
            continue
        names = _entry_target_names(target)
        drawn = [s for s in target.get("steps") or [] if _drawn(s)]
        hops: list[float | None] = [None] * len(drawn)
        if live is not None and hop_s is not None and drawn:
            hops = _shares(live * _visits_per_panel(target) * hop_s / 3600.0,
                           [_per_panel_s(s) for s in drawn])
        for step, hop_h in zip(drawn, hops):
            cycle = step.get("strategy") == "cycle"
            if cycle:
                filters = _slot_filters(step)
                goal = None
                sig: tuple = ("cycle", tuple(
                    (s.get("filter"), s.get("exposure_s"))
                    for s in step.get("slots") or []), step.get("cycles"),
                    step.get("per_cycle"), step.get("gain"),
                    step.get("binning"))
            else:
                filters = [str(step.get("filter") or "—")]
                goal = _num(step.get("integration_goal_h"))
                sig = (step.get("filter"), step.get("exposure_s"),
                       step.get("gain"), step.get("binning"),
                       step.get("count"), goal)
            if live is None:
                if sig in seen:
                    # Another pool candidate's copy of this exact step: the
                    # row already exists, so only its NAMES grow - the union
                    # of every member "best available" could still pick.
                    idx = seen[sig]
                    for n in names:
                        if n not in row_names[idx]:
                            row_names[idx].append(n)
                    continue
                seen[sig] = len(rows)
            tonight_h = _per_panel_s(step) / 3600.0
            per = 1 if live is None else live
            row = {
                "filter": ", ".join(filters) or "—",
                "goal_h": None if goal is None else round(goal * per, 2),
                "banked_h": None,  # filled below, once a row's names are final
                "tonight_h": round(tonight_h * per, 2),
                "has_ledger": have_ledger,
            }
            if cycle:
                row["strategy"] = "cycle"
                row["cycles"] = _count(step.get("cycles"))
            if live is not None:
                row["panels"] = live
                row["hop_h"] = hop_h
            rows.append(row)
            row_names.append(list(names))
            row_filters.append(filters)
    if have_ledger:
        for row, names, filters in zip(rows, row_names, row_filters):
            row["banked_h"] = banked_hours(names, filters)
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
    """``a and b``, ``a, b, and c``: the prototype's regex, spelled out.

    TWO ITEMS TAKE NO COMMA (#407). The Oxford comma belongs to a list of
    three or more; written before the last of two it read "panels 1-1, and
    3-2 skipped" in a mosaic's sentence, and "restores the filter, and
    resumes" in a hold's checklist of two steps."""
    if len(parts) <= 1:
        return "".join(parts)
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _duration(seconds: float) -> str:
    """"2 m 40 s", or "45 s" under a minute: the hop cost as the modal's
    readout writes it (spec 2.4), rounded to the second."""
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes} m {secs} s" if minutes else f"{secs} s"


def _minutes(seconds: float) -> str:
    """"13 min", or "8.5 min": a pass's length in the brief, to a tenth of a
    minute."""
    return f"{round(seconds / 60.0, 1):g} min"


def _mosaic_sentences(g: FlowGraph, block, hop_cost_s: float | None,
                      entry: dict | None = None) -> str:
    """One multi-panel TARGET, read back: its grid, the panels it skips, its
    overlap and angle, whether its panels rotate every pass (the loop wire,
    ``compile.loop_wires``, the compile's own reading) or run one at a time,
    how long a visit to a panel is, and what a hop between them costs (S3
    item 5, spec 1.2, 1.4, 3.3, 5.3).

    Every number is the node's param as typed, except the panel counts, which
    are the grid's and the skip list's, read by the compile's own
    ``_grid_of`` and ``parse_skip`` so the brief cannot count a panel the
    compile does not, and the visit (below). The hop is the MEASURED cost or
    the words "not measured yet", never the engine's seed.

    THE SIZE IS COLUMNS BY ROWS (S4 orchestrator ruling 1, #339), as the
    camera field is written width by height and as the Examples and the
    framing card write it: 2 rows of 3 columns is "3x2", so the eighth
    Example, "M31 3x2, rotating", briefs as a 3x2 and not the "2x3" S3
    wrote. Panel labels stay row-column ("2-1" is row 2, column 1), which
    is what pulled the size the other way. Where the size and a label share
    the sentence (the skipped panels), the size is said once in words, "3
    columns by 2 rows" (``compile.grid_size``, the compile note's own
    wording), and the labels are said to be row-column, so the two
    conventions cannot be read into each other.

    THE VISIT IS THE ONE THE RUN MAKES (spec 5.3, S3 item 5, #353). A visit
    ends at a round boundary once it has made ``passes`` rounds AND lasted
    ``minVisit``, so it makes ``max(passes, ceil(minVisit / pass))`` passes
    (``_visit_passes``, which is ``readouts.visit_passes``, the bound the
    Target modal prints). S3's sentence quoted ``passes`` alone, so a block
    at ``minVisit`` 30 on a 13-minute pass was said to move on after one
    pass while the run stayed for three. The pass is the block's own stages,
    which only the compile's scoping rule knows (spec 1.5), so ``entry`` is
    the block's compiled entry; with none (a compile that did not answer)
    the sentence quotes ``passes`` as typed, as it always did. The stages the
    pass is made of are each named by the brief's capture sentences
    (``_stage_sentence``, #395), so a reader can add them up to the pass this
    sentence states."""
    p = block.params
    rows, cols = _grid_of(block)
    skip, _unread = parse_skip(p.get("skip"), rows, cols)
    total = rows * cols
    name = str(p.get("name") or "").strip() or "This TARGET"
    if skip:
        gone = _join_and([f"{r}-{c}" for r, c in skip])
        t = (f"{name} is a mosaic of {grid_size(rows, cols)} shooting "
             f"{total - len(skip)} of its {total} panels "
             f"({'panel' if len(skip) == 1 else 'panels'} {gone} skipped, "
             f"written row-column)")
    else:
        t = f"{name} is a {cols}x{rows} mosaic of {total} panels"
    t += f" at {p.get('overlap')}% overlap"
    angle = target_angle(p)
    if angle == "Rotate to PA":
        t += (f", laid out at PA {p.get('rotation')}° with the rotator "
              f"turned to it at every panel")
    elif angle == "Camera fixed at PA":
        t += (f", laid out at PA {p.get('rotation')}° with the camera fixed "
              f"there by hand, and the run checks the angle at every panel")
    else:
        t += ", at no set angle, so its panels will not tile and it cannot run"
    seg = [t + "."]
    order = str(p.get("order") or "").lower()
    if loop_wires(g, block):
        passes = p.get("passes")
        visit = asked = None
        if entry is not None:
            visit = _visit_passes(entry)
            asked = _count((entry.get("mosaic") or {}).get("passes"))
        if visit is None or visit == asked:
            seg.append(f"After {passes} pass"
                       f"{'' if str(passes) == '1' else 'es'} of its filters "
                       f"on a panel it moves on to the next ({order}), and "
                       f"comes back until every panel has its subs.")
        else:
            seg.append(f"After {visit} passes of its filters on a panel it "
                       f"moves on to the next ({order}), and comes back until "
                       f"every panel has its subs.")
            seg.append(f"A visit is {visit} passes rather than the {passes} "
                       f"asked, since it lasts at least its "
                       f"{p.get('minVisit')} min minimum and a pass takes "
                       f"{_minutes(_pass_s(entry))}.")
    else:
        seg.append(f"It shoots one panel at a time, each finished before the "
                   f"next ({order}).")
    hop = _measured(hop_cost_s)
    seg.append(f"A hop between panels takes about {_duration(hop)}, as "
               f"measured on this rig." if hop is not None else
               "The hop between panels has not been measured on this rig "
               "yet.")
    return " ".join(seg)


def _mosaic_entries(graph: FlowGraph, plan: dict | None) -> dict[str, dict]:
    """Each multi-panel block's compiled entry, by node id: ``plan``'s, or
    the graph compiled here when the caller has none.

    A compile that raises gives no entries, and the brief then quotes the
    params as typed: the brief is prose about the graph, and a graph the
    compile cannot read is the compile route's to refuse, not a reason for
    the STORY tab to have no brief at all."""
    if plan is None:
        try:
            plan = compile_plan(graph)
        except Exception:       # noqa: BLE001 - see above
            return {}
    return {str(e.get("node_id")): e for e in plan.get("targets") or []
            if e.get("mosaic") and e.get("pool_rank") is None}


#: The node types that shoot lights, a sentence each in the brief.
_CAPTURE_TYPES = frozenset({"cycle", "capture"})

#: The node types that head a lane, an arm or a select sentence each in the
#: brief (#470).
_BLOCK_TYPES = frozenset({"target", "pool"})


def _brief_walk(g: FlowGraph) -> list:
    """Every block and capture stage of the graph, in the order the run
    cursor reaches them along the flow wires (``compile.flow_order``, with
    canvas order only between nodes no wire orders), so a lane drawn right
    to left still reads first stage first, and a block reads where its lane
    runs (#470).

    A node the walk never reaches (one inside a flow loop, which validation
    refuses but the editor can draw) follows in canvas order: the brief
    describes the graph the operator drew, and a node on the canvas is not
    left out of it because the compile drops it (see ``brief``)."""
    kinds = _BLOCK_TYPES | _CAPTURE_TYPES
    walked = [n for n in flow_order(g) if n.type in kinds]
    seen = {n.id for n in walked}
    rest = sorted((n for n in g.nodes if n.type in kinds and n.id not in seen),
                  key=lambda n: (n.x, n.y, n.id))
    return walked + rest


def _capture_stages(g: FlowGraph) -> list:
    """Every capture stage of the graph, in lane order: the stages of
    ``_brief_walk``, so a stage the walk never reaches follows in canvas
    order, as it always did."""
    return [n for n in _brief_walk(g) if n.type in _CAPTURE_TYPES]


def _receives(block) -> bool:
    """Does the compile give this TARGET or POOL any entry to append a step
    to? A TARGET always has one; a POOL has one per member, so a POOL whose
    members box names nobody has none (``compile_plan``'s pool branch), and
    a stage in its lane is shot for no one."""
    if block.type != "pool":
        return True
    return any(m.strip() for m in str(block.params.get("members") or "")
               .split(","))


def _receivers(g: FlowGraph) -> dict[str, tuple]:
    """The blocks each capture stage is shot for, by the compile's own
    scoping rule (``compile_plan``'s ``receivers``, spec 1.5, backlog
    WP-19(a)), keyed by the stage's node id.

    When the graph ``needs_wire_scoping`` (a multi-panel TARGET, or more
    than one TARGET/POOL block, #151), a stage is its ``owner_of`` block's
    alone, and nobody's when the chain reaches none (M13). With at most one
    block and none of them a mosaic, it is every block the walk passed
    before it: the canvas-order rule, which a single block can never leak
    out of. A stage the walk never reaches is absent, as the compile drops
    it.

    READ OFF THE GRAPH, not the compile: the brief compiles only a graph
    with a mosaic (``_mosaic_entries``), and a graph with none must brief
    without a compile it never needed. ``test_flows_brief_stage_owner.py``
    holds this to the plan's own steps."""
    scoped = needs_wire_scoping(g)
    out: dict[str, tuple] = {}
    passed: list = []
    for n in flow_order(g):
        if n.type in ("target", "pool"):
            if _receives(n):
                passed.append(n)
        elif n.type in _CAPTURE_TYPES:
            if not scoped:
                out[n.id] = tuple(passed)
                continue
            owner = owner_of(g, n.id)
            out[n.id] = ((owner,) if owner is not None and _receives(owner)
                         else ())
    return out


def _block_label(block) -> str:
    """A block as a stage sentence names it: a TARGET by its name, a POOL
    by what it expands to."""
    if block.type == "pool":
        return "every member of the pool"
    return str(block.params.get("name") or "").strip() or "an unnamed TARGET"


#: The lead of a stage that no block's lane holds, when the brief names
#: lanes: the compile appends its step to no target, so it shoots nothing
#: (the doctor's M13 says the same, "It would shoot nothing").
_NO_BLOCK = "Belonging to no TARGET, and so shooting nothing,"


def _stage_sentences(g: FlowGraph) -> dict[str, list[str]]:
    """Every capture stage's sentence (``_stage_sentence``), in lane order,
    each lane's stages together under the block they are shot for, keyed by
    the id of the lane's first stage, which is where ``brief`` says the lane
    (#470: a block's sentence reads where its lane runs, and so does the
    lane).

    ONE LANE READS AS IT ALWAYS DID: "It captures", then "It then
    captures". The stages of a flow whose stages all go to one block (every
    Example, and every single-target or pool-only flow) are said with no
    block named, byte for byte as before.

    SEVERAL LANES NAME THEIR BLOCK (#470, #395). Read as one chain, the
    stages of a flow of several blocks all read as the first block's: a
    rotating 2x2 M31 whose lane was a CAPTURE of Ha, beside a TARGET M33
    whose lane was a CAPTURE of L, said "It captures Ha 300 s × 4 ... It
    then captures L 60 s × 5 ...", 360 s of stages under a visit sentence
    that priced M31's pass at the 300 s the compile gives it, and M33 was
    never named. Now each lane's first stage says whose it is ("For M33 it
    captures L 60 s × 5 ..."), by the compile's own rule
    (``_receivers``), so the stages named after a mosaic's sentences are
    the stages its pass is made of. A stage the compile gives to nobody
    says so (``_NO_BLOCK``). The lanes follow in the order their first
    stage runs, and a lane's stages keep lane order."""
    stages = _capture_stages(g)
    receivers = _receivers(g)
    lanes: dict[tuple, list] = {}
    for s in stages:
        key = tuple(b.id for b in receivers.get(s.id, ()))
        lanes.setdefault(key, []).append(s)
    if len(lanes) <= 1:
        return ({stages[0].id: [_stage_sentence(s, i == 0)
                                for i, s in enumerate(stages)]}
                if stages else {})
    out: dict[str, list[str]] = {}
    for lane in lanes.values():
        blocks = receivers.get(lane[0].id, ())
        who = (f"For {_join_and([_block_label(b) for b in blocks])}"
               if blocks else _NO_BLOCK)
        out[lane[0].id] = [_stage_sentence(s, i == 0,
                                           who=who if i == 0 else None)
                           for i, s in enumerate(lane)]
    return out


def _stage_sentence(node, first: bool, who: str | None = None) -> str:
    """One capture stage, read back: a FILTER CYCLE's slot table and the
    subs each filter takes a pass, or a CAPTURE LOOP's filter, exposure,
    count, gain and bin. The first stage is worded as the brief always
    worded its one stage; a later one says "It then", so a lane of several
    reads in the order it runs.

    A CYCLE SAYS HOW MANY SUBS A PASS (#395). The sentence said "one sub per
    filter per pass" whatever ``perCycle`` held, so a cycle at two a pass was
    named at half the pass the visit sentence prices. The count is the
    compile's own reading (``_finite``, a count that is no finite number
    read as 1), so the sentence and the pass cannot read one value two ways.

    NO GRADING CLAUSE. The cycle's sentence used to end "; a sub is graded
    and only counts below HFR {reject}″" — a specific threshold, in arcsec,
    for something nothing does: the node's `reject` is dropped by `to_plan`
    and the plan's nearest field is a multiple of the running median, not an
    absolute HFR. An operator reading it would believe soft frames were
    being discarded and their counts topped up. What DOES grade a frame is
    the rig's own standards, which are not this flow's to describe; that
    the setting is dropped is said where dropped settings are said, in
    `to_plan.INERT_PARAMS`. The capture's sentence lost the same clause for
    the same reason.

    ``who`` opens a lane's first stage with the block it is shot for ("For
    M33", or ``_NO_BLOCK``) where the brief names lanes
    (``_stage_sentences``), and then ``first`` is not read."""
    p = node.params
    if node.type == "cycle":
        table = ", ".join(f"{f} {e} s × {p.get('cycles')}"
                          for f, e in parse_cycle_plan(p.get("plan")))
        per = max(1, int(_finite(p.get("perCycle"), 1) or 1))
        each = "one sub" if per == 1 else f"{per} subs"
        lead = (f"{who} it interleaves" if who else
                "Capture interleaves" if first else "It then interleaves")
        return (f"{lead} {each} per filter per pass - {table} - so every "
                f"channel grows evenly.")
    lead = (f"{who} it captures" if who else
            "It captures" if first else "It then captures")
    return (f"{lead} {p.get('filter')} {p.get('exposure')} s × "
            f"{p.get('count')} (gain {p.get('gain')}, bin {p.get('bin')}).")


def _block_sentence(block, lead: str) -> str:
    """A block's own sentence in the brief: a POOL selects the best of its
    members by its gates, and a TARGET is armed, each worded as the brief
    always worded the one block it named (#470 gave every block one).

    "IT THEN" NEEDS SOMETHING TO FOLLOW, so ``lead`` is the caller's. The
    prototype opened this sentence with a fixed "It then", which reads
    correctly after the arming sentence and is broken English without one -
    the EAA example has no DUSK WINDOW, so its brief began "It then arms M27 -
    Dumbbell." with no antecedent. Same sentence, same content, correct
    connective; noted in the milestone summary as a prototype defect rather
    than a design change."""
    p = block.params
    if block.type == "pool":
        return (f"{lead}selects the best of {p.get('members')} - above "
                f"{p.get('minAlt')}°, at least {p.get('moonSep')}° from the "
                f"moon (if up), within {p.get('maxHA')} h of the meridian.")
    return f"{lead}arms {p.get('name')}."


def _frames(n: int) -> str:
    return "frame" if n == 1 else f"{n} frames"


def _guide_clause(rig: RigFacts | None) -> str:
    """The brief's words for a GUIDE stage, from the rig facts (#506).

    "guides with AstroDeck native (settle below 1.5 px for 10 s, dither 3 px
    every 3 frames)": the provider as the guide resolver labels it, the
    settle every dither waits on, and the dither distance and cadence the
    run uses (``RigFacts``' ``guide_*`` fields, read by the route from the
    rig, the source ``to_plan.NODE_SETTINGS["guide"]`` cites). Pixels, not
    arcseconds: a settle is measured on the guide camera, where the old
    sentence's ″ had no image scale behind it. A fact the rig could not
    give is left out rather than guessed, and a guider that settles by its
    own rule (NINA) is said to. With no provider there is nothing to name,
    so the clause says where the guider, settle and dither come from and
    prints no number."""
    provider = rig.guide_provider if rig is not None else None
    if not provider:
        return "guides (guider, settle and dither from Rig > Guider)"
    parts: list[str] = []
    if rig.guide_settle is not None:
        px, secs = rig.guide_settle
        parts.append(f"settle below {px:g} px for {secs:g} s")
    else:
        parts.append(f"{provider}'s own settle")
    if rig.guide_dither_every is not None and rig.guide_dither_px is not None:
        parts.append("no dither" if rig.guide_dither_every == 0 else
                     f"dither {rig.guide_dither_px:g} px every "
                     f"{_frames(rig.guide_dither_every)}")
    return f"guides with {provider} ({', '.join(parts)})"


def brief(graph: FlowGraph | None, *, hop_cost_s: float | None = None,
          plan: dict | None = None, rig: RigFacts | None = None) -> str:
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

    NO SLEW SENTENCE (S3, spec 1.7). It read "slews and plate-solves to
    within {tol}′ ({solver})" off a SLEW + CENTER node, whose tolerance and
    solver never reached the run: every run centred to the hub's 0.02 deg,
    and since S3 to the TARGET's own centring. A legacy SLEW left on a canvas
    is the doctor's to name (L1), not a promise for the brief to repeat.

    THE GUIDE SENTENCE SAYS WHAT THE RUN WILL DO, FROM THE RIG (#506). The
    GUIDE stage decides one thing, that the night guides (#239 stage C), and
    the compile's own note for it says the card's settle, dither and provider
    are ignored and "come from Rig > Guider instead". The brief read "guides
    with PHD2 (settle below 1.5″, dither every 3 frames)" off those very
    params, the card's defaults, on a rig guiding with its native guider, so
    the one sentence read before a night contradicted the run-start answer's
    ``unmapped`` list. Now it names what ``rig`` says (``_guide_clause``):
    the provider the resolver picks, the settle every dither waits on and
    the dither distance and cadence the run uses. With no rig facts it names
    none of them and says where they come from.

    EVERY CAPTURE STAGE GETS A SENTENCE (#395), in lane order
    (``_capture_stages``, ``_stage_sentence``): a FILTER CYCLE then a CAPTURE
    LOOP reads as both, and two CAPTURE LOOPs as two. It named only the
    first cycle or, with none, the first capture. Where the stages go to
    more than one block, each lane's first stage names its block by the
    compile's own scoping rule (``_stage_sentences``, #470).

    EVERY BLOCK GETS A SENTENCE, WHERE ITS LANE RUNS (#470). Each TARGET is
    armed and each POOL selects (``_block_sentence``), in the order the run
    cursor reaches them (``_brief_walk``, ``compile.flow_order``), and each
    lane of stages follows where its first stage runs. It wrote one, the
    first POOL's or else the first TARGET's, ahead of every lane.

    A DUSK OFFSET THAT IS NO FINITE NUMBER IS SAID TO BE UNREADABLE (#423),
    where ``int()`` of it raised and took the whole Tonight answer down.

    A MOSAIC GETS ITS OWN SENTENCES (``_mosaic_sentences``), one set per
    multi-panel TARGET, after the target sentence. ``hop_cost_s`` is the
    measured hop cost the route injects; with none, they say it is not
    measured yet. ``plan`` is the graph's compile, which ``resolve_tonight``
    already holds: the one number the brief reads off it is a visit's
    length, because that is a sum over the block's own stages (spec 5.3).
    Without it, and only for a graph with a mosaic, the graph is compiled
    here.
    """
    if graph is None:
        return ""
    g = graph.with_defaults()
    n = lambda t: _first(g, t)                                   # noqa: E731
    dusk, pool, rep = n("dusk"), n("pool"), n("report")
    cw, hold, cq, pc = n("cloudwatch"), n("holdresume"), n("calib"), n("parkclose")
    # No `dome = n("dome")` here (#192): the arm sentence used to add a dome
    # clause ("opens the dome and binds it to the mount") that nothing in the
    # engine does, and the node has no OTHER true thing to say at arm time -
    # its real effect (the unsafe-close path) belongs to the STORY row, not
    # this brief.
    saf, df = n("safety"), n("duskflats")
    guide, af = n("guide"), n("autofocus")
    # WHETHER THIS FLOW IS A CAMPAIGN is the compile's own block, read through
    # the one function that writes it (`compile.campaign_block`, #195, WP-118):
    # a POOL in a flow whose Automatic resume is On. The two campaign sentences
    # below keyed on DUSK's `repeat` until then.
    campaign = campaign_block(g)
    seg: list[str] = []

    if dusk is not None:
        p = dusk.params
        start = _START_PROSE.get(str(p.get("start")), str(p.get("start")))
        t = f"This flow arms at {start}"
        # THE OFFSET IS READ FINITE-ONLY (#423), as the compile reads it into
        # ``start_offset_min`` (``_finite``). ``int()`` of it as ``_num``
        # read it raised on a stored "inf" or JSON ``1e999`` ("OverflowError:
        # cannot convert float infinity to integer") and on "nan"
        # (ValueError), and ``_num`` itself raised on a 400-digit integer; a
        # DUSK offset is no count, so validation lets a save store any of
        # them, and ``resolve_tonight`` calls this unconditionally, so the
        # whole Tonight answer raised for such a flow. The compile reads each
        # as 0, so the run applies no offset, and the brief says so rather
        # than print no offset without a word for one the node plainly holds.
        raw = p.get("offset")
        if _not_finite(raw):
            t += (f" (its offset, {_shown(raw)}, cannot be read as a number "
                  f"of minutes, so none is applied)")
        else:
            off = int(_finite(raw))
            if off:
                t += f" ({_signed(off, '+.0f')} min)"
        # NOT ", opens the dome and binds it to the mount" (#192): nothing in
        # the engine drives either at arm time - the only open_shutter call is
        # the reopen after an unsafe close (opt-in, off by default), and
        # DomePolicy.apply_binding has no caller - so this clause said
        # something the run would not do. A DOME node's safety effect (the
        # unsafe-close path) is covered by the "2. the dome" STORY row rather
        # than repeated here.
        # THE CLAUSE FOLLOWS THE ENGINE (#192, #603 job A), not the node: no
        # stage runs a DUSK FLATS block, so ", and shoots N flats per filter
        # (...) in the twilight window" promised frames the run would not
        # take, beside the compiler's own "this run will not take flats".
        # The count and the method are no longer quoted for the same reason:
        # a figure printed beside "does not run" reads as a plan.
        if df is not None:
            if _dusk_flats_wired():
                t += (f", and shoots {df.params.get('count')} flats per "
                      f"filter ({str(df.params.get('method')).lower()}) in "
                      f"the twilight window")
            else:
                t += (", and has a DUSK FLATS block that the engine does not "
                      "run yet, so no flats are taken")
        seg.append(t + ".")

    # `stages`, not `rig`: `rig` is the rig facts handed in.
    stages: list[str] = []
    if af is not None:
        stages.append(f"autofocuses ({str(af.params.get('method')).lower()})")
    if guide is not None:
        # The stage's presence, with the rig's guider (#506). Never the
        # card's `provider`, `settle` or `dither`: none of them reaches the
        # run (`to_plan.NODE_SETTINGS["guide"]`).
        stages.append(_guide_clause(rig))
    rig_owed = [] if not stages else [
        "For each target it " + ", ".join(stages) + "."]

    # EVERY BLOCK, WHERE ITS LANE RUNS (#470). This wrote one arm sentence,
    # the first POOL's or, with none, the first TARGET's, ahead of every
    # lane, so a TARGET after the first was never named and a POOL's sentence
    # came first wherever its lane ran: the S4 band fixture's graph, M16's
    # mosaic, then M31, then a POOL, read "It then selects the best of M13,
    # M92 ..." straight after the arming sentence and never said M31. Now the
    # walk (``_brief_walk``, the run cursor's order) gives each block its
    # sentence, and a multi-panel TARGET its mosaic sentences, where the
    # cursor reaches it, and each lane of stages (``_stage_sentences``)
    # where its first stage runs, so the blocks and their lanes read in the
    # order the night runs them. A flow of one block reads as it always did:
    # its sentence, its mosaic's, the rig sentence, then its stages.
    #
    # EVERY CAPTURE STAGE, IN LANE ORDER (#395). This picked one stage, the
    # first FILTER CYCLE or, with none, the first CAPTURE LOOP, so a lane of a
    # cycle then a capture never named the capture, and a lane of two
    # captures named the first: a stage the operator drew was dropped without
    # a word, and since S4 the visit sentence priced its pass out of stages
    # the brief did not name ("a pass takes 10 min" of a lane whose one named
    # stage takes 5). A flow of several lanes names each lane's block, so no
    # lane's stages read as the first block's (#470, ``_stage_sentences``).
    #
    # The rig sentence is flow-wide ("For each target"), and is said once,
    # before the first lane's stages, where a flow of one block always said
    # it; with no stage at all, after the last block.
    lanes = _stage_sentences(g)
    multi = any(is_multi_panel(b) for b in g.nodes)
    entries = _mosaic_entries(graph, plan) if multi else {}
    for node in _brief_walk(g):
        if node.type in _BLOCK_TYPES:
            lead = "It then " if seg else "This flow "
            seg.append(_block_sentence(node, lead))
            if is_multi_panel(node):
                seg.append(_mosaic_sentences(g, node, hop_cost_s,
                                             entries.get(node.id)))
        elif node.id in lanes:
            seg.extend(rig_owed)
            rig_owed = []
            seg.extend(lanes[node.id])
    seg.extend(rig_owed)

    advances = pool is not None and _wired(g, to=pool, to_port="advance")
    if rep is not None and advances:
        seg.append("When a target's quota is met, a session report is cut and "
                   "the pool advances to the next best - finished targets are "
                   "never re-selected.")
    elif rep is not None:
        seg.append("A session report is appended when the run ends.")

    # GATED ON THE DIAL ALONE, because the dial is the only half the engine
    # reads (`Schedule.on_floor`, set from `onFloor` in compile.py). The old
    # condition also fired on a WIRED floor port, so a graph that wired the port
    # and then chose "Keep imaging" got this paragraph promising the opposite of
    # what its own dial said - and for a while nothing implemented either.
    #
    # WORDED AS THE ENGINE'S FLOOR LINE IS (#208, #189 S2). Since S2 the floor
    # advance is a `Session.set_aside` record under tonight's night key, which
    # a restart or an auto-resume tonight reads back and a run on any other
    # night ignores. Before S2 this sentence promised more than engine memory
    # kept. test_set_aside_promises.py maps it to the tests that prove each
    # half.
    if pool is not None and str(pool.params.get("onFloor") or "").strip() \
            .lower().startswith("advance"):
        seg.append(f"If the active target sinks to the "
                   f"{pool.params.get('minAlt')}° floor, it is set aside for "
                   f"tonight - a restart tonight does not retry it, the next "
                   f"night does - and the next best takes over.")

    if cw is not None:
        # NO PERCENTAGE (#707). The CLOUD WATCH node's threshold dial never
        # reaches the engine: `_eval_predicate` answers `clouds_in` with the
        # detector's own verdict and `to_plan` reports the dial as dropped
        # (BOOLEAN_TRIGGERS, "does not reach the engine"), so "cloud cover
        # above 40%" quoted a number the run never acts on.
        #
        # THE QUEUE CLAUSE SAYS WHAT A HOLD DOES (#603 job A): it shoots
        # darks, matched to the step it interrupts and capped at what the
        # library still needs (`_hold_darks`). The queue's order, its bias
        # leg and its flats leg are not wired (`to_plan._automation`), so the
        # old "(darks → bias → flats-if-panel)" listed two stages that never
        # run. Not tied to `_dusk_flats_wired`: these are the QUEUE's legs,
        # a different stage from the DUSK FLATS block.
        t = ("If cloud cover is detected (this trigger fires on the cloud "
             "detector's own verdict), imaging pauses at the frame boundary")
        if cq is not None:
            t += (" and the calibration queue takes darks for whatever the "
                  "library lacks (its bias and flat legs are not run yet)")
        # NO MINUTES, THE SAME CLASS ONE CLAUSE LATER (#743). The sentence
        # used to read "once the sky holds clear for {clearFor} min", quoting
        # the CLOUD WATCH node's "Clear must hold" dial. Nothing reads that
        # dial: `_hold_for_clear` releases a hold after
        # `CLOUD_RESUME_CLEAR_PROBES` consecutive clear check frames, and
        # `to_plan` carries no `clearFor` into the plan, so an operator who
        # read "4 min" and moved the dial to 15 changed the brief and not the
        # night. The clause now says what the engine does, in the engine's
        # own terms (a streak of check frames, which the probe loop takes
        # every `CLOUD_PROBE_EVERY_S`), with no number to go stale.
        # `test_the_clear_for_dial_reaches_no_plan_field` holds the premise: the
        # day the dial is carried to the engine, it goes red and the number
        # can come back.
        t += "; once consecutive check frames read clear it"
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
        # WHAT ACTUALLY HAPPENS, not what the node's dials say.
        #
        # This sentence used to add "with the cooler held cold" whenever the
        # PARK + CLOSE node's `cooler` dial said Hold, and ", banking capped day
        # darks" whenever it was wired to a calibration queue. Neither happens:
        # `plan_extras` hardcodes `warm_cooler_when_done=True` for every
        # flow-derived plan, and no lane shoots darks after a shutdown at all -
        # the calibration queue's darks are only ever taken inside a cloud hold.
        #
        # The campaign tab already said so in `_DAWN`, so the two tabs of the
        # same page contradicted each other about the same rig on the same
        # night. This is the one that was wrong.
        #
        # The dropped dials belong in the unmapped list, not in a hedge here -
        # a story that says "the cooler MIGHT be held" is worse than one that
        # says what the night does. See `_automation`'s parkclose note.
        t = (f"When astronomical night ends, the mount parks and the "
             f"{str(pc.params.get('closure')).lower()} closes")
        t += ", then the camera warms"
        # A CAMPAIGN, WHICH IS NEVER AN OFF FLOW (#195): an Off plan is
        # disarmed where its night ends, so a later dusk resumes nothing, and
        # this sentence would be a claim nothing keeps. `campaign_block` is
        # None for it, so the clause is not said.
        if campaign is not None:
            t += ("; the flow re-arms at the next dusk and resumes mid-cycle "
                  "from the session log")
        seg.append(t + ".")

    if saf is not None:
        seg.append("Rain, wind, or power failure aborts and parks "
                   "unconditionally - a stale reading counts as unsafe.")

    if (campaign is not None and pool is not None
            and campaign.get("until") == CAMPAIGN_UNTIL):
        members = [m for m in str(pool.params.get("members") or "").split(",")
                   if m.strip()]
        # THE QUOTA IS NAMED ONLY WHERE IT GOVERNS (WP-118). The pool's
        # `quota` is a count of CYCLES and reaches the run through a FILTER
        # CYCLE stage alone (`to_plan._quota_cycles`); a CAPTURE LOOP under a
        # pool ends each member at its own frame count and never reads it.
        # This sentence was said only for a stored `repeat` flow, in practice
        # the campaign Example's cycle, so "the 45-cycle quota" was true where
        # it appeared. Keyed on the campaign block it reaches every pool flow
        # with Automatic resume On, a capture-loop pool included, and quoting
        # the dial there would promise a finish line the run does not keep.
        if any(x.type == "cycle" for x in g.nodes):
            seg.append(f"Once all {len(members)} targets hold their "
                       f"{pool.params.get('quota')}-cycle quota, the rig "
                       f"stays parked.")
        else:
            seg.append(f"Once all {len(members)} targets have the frames "
                       f"they ask for, the rig stays parked.")

    return " ".join(seg)


def frames_by_target_from_reports(reports: Iterable[Any]
                                  ) -> dict[str, dict[str, int]]:
    """``{target: {filter: accepted_frames}}``, summed over the ledger.

    The pure half of the CAMPAIGN tab's progress, matching
    ``banked_hours_from_reports`` in shape and for the same reason: the route
    supplies the impure half, the reports' ledger summaries
    (``SessionReporter.summaries``, which carry each report's ``targets``,
    #536), and this module stays callable without a disk. No ``targets``
    filter is needed here: the answer is keyed by target, and a pool member
    reads only its own row.

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


def banked_hours_by_target_from_reports(reports: Iterable[Any]
                                        ) -> dict[str, dict[str, float]]:
    """``{target: {filter: hours}}``, summed over the ledger.

    The per-BLOCK half of ``banked_hours_from_reports`` (#562). That fold
    answers one ``{filter: hours}`` mapping for the WHOLE flow, so in a flow
    of several blocks sharing a filter, every BUDGET row read the flow's
    total rather than its own block's: an M16 Ha row and an M31 Ha row in the
    same flow both read the SUM of M16's and M31's Ha, so a block that had
    banked nothing still read as progressing on the other block's frames.

    This fold keeps the per-target breakdown instead of collapsing it, in the
    same shape ``frames_by_target_from_reports`` already keeps for accepted
    frames and for the same reason: the route supplies the impure half (the
    reports' ledger summaries), this module stays callable without a disk,
    and ``_budget`` reads only the names its own row stands for
    (``_entry_target_names``), so M16's hours never reach M31's row.
    """
    out: dict[str, dict[str, float]] = {}
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
                bucket[str(filt)] = bucket.get(str(filt), 0.0) + _num(
                    _field(fb, "integration_s", 0.0)) / 3600.0
    return out


#: What every campaign note says about the end of a night, in ONE place because
#: it was said three times and two of the three clauses were untrue.
#:
#: It read "Dawn parks + closes; the cooler stays cold for day darks; each dusk
#: resumes mid-cycle."
#:
#: * PARKS is true - `plan_extras` sets `park_when_done=True` for every flow.
#: * CLOSES is not. No dome action reaches the engine; `to_plan` reports that
#:   separately at danger weight, and this line was quietly contradicting it.
#: * THE COOLER STAYS COLD is not either, and it is the expensive one to
#:   believe: `plan_extras` also sets `warm_cooler_when_done=True`, so the TEC
#:   ramps up at dawn. An operator who read this and left the rig expecting a
#:   cold sensor for day darks would come back to a warm one and a dark library
#:   indexed at a temperature the frames do not have.
#: * RESUMES MID-CYCLE is true - the session ledger seeds the next night.
#:
#: The design asks for the cooler to stay cold, and that is a real request, but
#: answering it means changing what the run does rather than what this sentence
#: claims. Recorded on the campaign item; the copy tells the truth meanwhile.
_DAWN = ("Dawn parks the mount and warms the camera - the dome is not driven "
         "and the cooler does not stay cold for day darks. Each dusk resumes "
         "where the session log left off.")

#: What a flow that turned DUSK WINDOW's Automatic resume Off says in place of
#: the last sentence (#195, owner ruling 7 on #189): the engine disarms such a
#: session where its night ends (`_finalize_report`), so "each dusk resumes"
#: would be untrue of it. ONE phrase, said by `_dawn_note` and by the budget
#: rows, so the CAMPAIGN tab and the STORY tab cannot disagree about the same
#: flow.
_NO_LATER_RESUME = ("a subsequent night does not resume by itself; CONTINUE it "
                    "by hand")
_DAWN_OFF = ("Dawn parks the mount and warms the camera - the dome is not "
             "driven and the cooler does not stay cold for day darks. A "
             "subsequent night does not resume by itself; CONTINUE it by hand.")


def _dawn_note(resumes: bool) -> str:
    """``_DAWN``, or ``_DAWN_OFF`` for a flow whose plan does not resume on
    subsequent nights."""
    return _DAWN if resumes else _DAWN_OFF


#: The CAMPAIGN note's words for the forecast nobody makes, said once so the
#: pool's note and a mosaic's cannot drift apart.
_NOT_FORECAST = ("Nights to finish are not forecast - clear-sky prediction "
                 "that far out is not something this rig models.")


def _read_progress(progress: ProgressSource) -> Mapping[str, Any] | None:
    """The progress answer, called if it is a callable; None when there is
    none or reading it failed. A progress read that raises (a session file
    that will not load, ``flow_progress`` refusing a plan it cannot account
    for) is NO PROGRESS, as a ledger that raises is no ledger: the tab must
    not claim a count, and must not take the Tonight panel down with it."""
    if progress is None:
        return None
    try:
        got = progress() if callable(progress) else progress
    except Exception:       # noqa: BLE001 - see the docstring: no count, no 500
        return None
    return got if isinstance(got, Mapping) else None


def _panel_rows(mosaics: list, progress: ProgressSource) -> list[dict] | None:
    """The CAMPAIGN tab's rows for a flow's mosaics, one per panel, from the
    progress answer (``progress.flow_progress``), or None with no progress.

    A row is ``{block, name, row, col, banked, owed, total, done, pct,
    skipped}``: a shot panel's numbers are the progress answer's own subs,
    banked (capped at each step's count), owed and total, counted by the
    session's count mode, so the tab and the card's chip can never disagree;
    ``done`` is a panel that owes nothing. A skipped panel follows its
    block's shot panels with ``skipped`` true, owing nothing and holding
    ``banked``, what the ledger has on it, so the skip does not hide the
    subs that re-enabling it brings back.

    SUBS, NOT CYCLES. A pool member's row counts complete cycles, because the
    pool's quota is set in cycles; a panel's quota is its steps' counts, and
    the progress answer counts those in subs. Only the blocks that are a
    multi-panel TARGET in THIS graph are read, by node id, so an answer for
    another flow puts no row here."""
    answer = _read_progress(progress)
    if answer is None:
        return None
    ids = {n.id for n in mosaics}
    rows: list[dict] = []
    for block in answer.get("blocks") or []:
        node_id = block.get("node_id")
        if node_id not in ids:
            continue
        for p in block.get("panels") or []:
            banked = int(_num(p.get("banked")))
            owed = int(_num(p.get("owed")))
            total = int(_num(p.get("total")))
            rows.append({
                "block": node_id, "name": p.get("name"),
                "row": p.get("row"), "col": p.get("col"),
                "banked": banked, "owed": owed, "total": total,
                "done": total > 0 and owed == 0,
                "pct": min(100, round(100 * banked / total)) if total else None,
                "skipped": False})
        for p in block.get("skipped") or []:
            rows.append({
                "block": node_id, "name": p.get("name"),
                "row": p.get("row"), "col": p.get("col"),
                "banked": int(_num(p.get("banked"))), "owed": 0, "total": 0,
                "done": False, "pct": None, "skipped": True})
    return rows


def _panels_clause(rows: list[dict] | None) -> str:
    """The note's sentence about a flow's mosaic panels: how many are done,
    the subs banked of the subs asked, and what the skipped ones hold; or
    that no progress was read, so no count is claimed."""
    if rows is None:
        return ("No progress was read for this flow's mosaic, so no panel's "
                "count is claimed.")
    shot = [r for r in rows if not r["skipped"]]
    skipped = [r for r in rows if r["skipped"]]
    done = sum(1 for r in shot if r["done"])
    t = (f"{done} of {len(shot)} mosaic panels done, "
         f"{sum(r['banked'] for r in shot)} of {sum(r['total'] for r in shot)}"
         f" subs captured")
    if skipped:
        held = sum(r["banked"] for r in skipped)
        one = len(skipped) == 1
        t += (f"; {len(skipped)} skipped panel{'' if one else 's'} "
              f"hold{'s' if one else ''} {held} more, which come back when "
              f"re-enabled")
    return t + "."


def _campaign(graph: FlowGraph | None,
              frames_by_target: Callable[[], Mapping[str, Mapping[str, int]]] | None,
              progress: ProgressSource = None) -> dict:
    """The CAMPAIGN tab: how much of the pool's quota each member has banked,
    and how far each panel of a mosaic has got.

    Returns ``{is_campaign, has_pool, has_ledger, quota, members[], note}``.
    A member row is ``{name, banked, quota, done, pct}``.

    A MOSAIC NEEDS NO POOL (S3 item 5, #189 U-10). A flow with a multi-panel
    TARGET adds ``panels``, one row per panel from the injected ``progress``
    (``_panel_rows``), and ``has_progress``; with no pool its answer is the
    panels' (``has_pool`` stays False, because there is none, and the note
    speaks of panels), where it used to be "campaigns need one" and nothing
    else. A flow with no mosaic never reads ``progress`` and answers exactly
    as it did.

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
    mosaics = [n for n in g.nodes if is_multi_panel(n)] if g is not None else []
    # A CAMPAIGN IS THE COMPILE'S OWN BLOCK (#195, WP-118): `campaign_block`
    # is the one function that writes the plan's `campaign` key, so the row
    # says "campaign" exactly when the plan does, and never from `repeat`,
    # which no editor offers any more. That is a POOL in a flow whose
    # Automatic resume is On; a mosaic with no pool is not one, whatever the
    # DUSK WINDOW holds.
    is_campaign = g is not None and campaign_block(g) is not None
    # WHETHER THE FLOW COMES BACK ON A SUBSEQUENT NIGHT is DUSK WINDOW's Automatic
    # resume (#195), read as the compile reads it (`dusk_auto_resume`), so
    # the note and the plan cannot disagree. A flow with no DUSK WINDOW
    # carries no opinion and resumes, as it always has.
    resumes = dusk is None or dusk_auto_resume(dusk.params)
    dawn = _dawn_note(resumes)

    if pool is None and mosaics:
        rows = _panel_rows(mosaics, progress)
        clause = _panels_clause(rows)
        return {"is_campaign": False, "has_pool": False, "has_ledger": False,
                "quota": 0, "members": [],
                "note": (f"{clause} {_NOT_FORECAST} {dawn}" if rows is not None
                         else f"{clause} {dawn}"),
                "panels": rows or [], "has_progress": rows is not None}
    if pool is None:
        return {"is_campaign": False, "has_pool": False, "has_ledger": False,
                "quota": 0, "members": [],
                "note": "No target pool in this flow - campaigns need one."}

    # A COUNT THAT IS NO COUNT IS REFUSED IN WORDS (#362 item 4,
    # ``_stored_count``): no member is counted, the note says which count
    # and why, and a refused quota is None, never an invented 45.
    quota, bad_quota = _stored_count(pool, "quota", 45)
    names = [m.strip() for m in str(pool.params.get("members") or "").split(",")
             if m.strip()]
    slots = parse_cycle_plan(cyc.params.get("plan")) if cyc is not None else []
    per_pass, bad_per = (_stored_count(cyc, "perCycle", 1)
                         if cyc is not None else (1, None))
    refusals = [r for r in (bad_quota, bad_per) if r is not None]

    bank: Mapping[str, Mapping[str, int]] = {}
    has_ledger = frames_by_target is not None
    if frames_by_target is not None:
        try:
            bank = frames_by_target() or {}
        except Exception:
            has_ledger = False

    def cycles_for(name: str) -> int | None:
        if not has_ledger or refusals:
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
        # THIS NOTE USED TO SAY "set DUSK WINDOW → Repeat to make this a
        # campaign" (#195). The editor no longer offers Repeat, and a flow
        # nobody has touched IS resuming (Automatic resume defaults On), so it
        # says which of the two this flow is. A pool in a flow whose Automatic
        # resume is On is a campaign (`campaign_block`, WP-118), so the only
        # pool flows that reach this branch are the Off ones, and one with no
        # DUSK WINDOW at all, which carries no opinion and resumes as it
        # always has.
        if resumes:
            note = ("Automatic resume is on (DUSK WINDOW): a subsequent night "
                    "resumes this flow where the session log left off.")
        else:
            note = ("Automatic resume is off (DUSK WINDOW → Automatic "
                    f"resume): {_NO_LATER_RESUME}, or turn the option on.")
    elif not has_ledger:
        note = ("The session log is unavailable, so captured totals cannot be shown. "
                f"{dawn}")
    elif not slots:
        note = ("This campaign's capture stage is not a FILTER CYCLE, so "
                f"progress is not counted in cycles. {dawn}")
    elif refusals:
        # Nothing is counted, so no work left can be stated.
        note = dawn
    else:
        left = sum(max(0, quota - (m["banked"] or 0)) for m in members)
        passes = left * per_pass * len(slots)
        note = (f"{left} cycles left across the pool ({passes} subs). Nights to "
                f"finish are not forecast - clear-sky prediction that far out is "
                f"not something this rig models. {dawn}")
    if refusals:
        note = f"{' '.join(refusals)} {note}"

    out = {"is_campaign": is_campaign, "has_pool": True,
           "has_ledger": has_ledger, "quota": quota, "members": members,
           "note": note}
    if mosaics:
        # A pool AND a mosaic: the pool's answer, with the panels beside its
        # members and one sentence about them after its note.
        rows = _panel_rows(mosaics, progress)
        out.update(note=f"{note} {_panels_clause(rows)}", panels=rows or [],
                   has_progress=rows is not None)
    return out


def _story(out: dict, plan: dict, graph: FlowGraph | None) -> list[dict]:
    """The STORY tab: the night as sentences, in the prototype's order.

    Rows carry ``t_unix`` and a ``label``; the label is "" for a timed row and
    "ANY"/"BUDGET" for the two special kinds, so the panel's time column shows
    a clock or a keyword without this module ever formatting one (see the
    module docstring on whose timezone that is).
    """
    night, automation = out["night"], (plan.get("automation") or {})
    sched = plan.get("schedule") or {}
    start_mode = str(sched.get("start_mode") or "")
    stop_mode = str(sched.get("stop_mode") or "")
    dusk, dawn = night["dusk_unix"], night["dawn_unix"]
    tw = out["twilight_deg"]
    timed: list[dict] = []
    rules: list[dict] = []

    def row(t_unix, msg, tone=TONE_TEXT, label=""):
        return {"t_unix": t_unix, "label": label, "msg": msg, "tone": tone}

    # 1. the window opens
    if start_mode == "dusk":
        offset = _num(sched.get("start_offset_min"))
        applied = (f", {_signed(offset, '+.0f')} min offset applied" if offset
                   else "")
        timed.append(row(night["window_start_unix"],
                         f"Autorun window opens (sun {_signed(tw)}°{applied})"))
    elif start_mode == "time":
        # #191: before this, a DUSK WINDOW's "Clock time" Start fell into the
        # `else` below and told the operator there was no window at all,
        # although the compile had a real start time for it.
        timed.append(row(night["window_start_unix"],
                         "Autorun window opens at the set clock time"))
    else:
        timed.append(row(out["now_unix"],
                         "No dusk window in this flow - the run starts when you "
                         "press RUN, and stops when you stop it", TONE_DIM))

    # 2. the dome, which is not the flow's to countermand. NOT "opens,
    #    azimuth bound to the mount" (#192): nothing drives either - the only
    #    open_shutter call is the reopen after an unsafe close, opt-in and off
    #    by default, and DomePolicy.apply_binding has no caller. The close
    #    half is what the compile actually enforces: to_plan refuses a DOME
    #    node with a connected dome unless close_dome_on_unsafe is set, so for
    #    any flow that runs with one, this sentence's remainder holds.
    if automation.get("dome"):
        timed.append(row(night["window_start_unix"],
                         "Dome: any unsafe or stale safety reading closes it, "
                         "whatever the flow is doing", TONE_DIM))

    # 3. dusk flats, if the sky is bright enough for long enough - and if the
    #    engine runs them. It does not (#192, #603 job A: no dusk-flats stage),
    #    so the row said "Flats window (...): ... flats, exposure solved to
    #    28 500 ADU per filter" about a block that takes none, beside the
    #    compiler's "this run will not take flats". While
    #    ``to_plan.DUSK_FLATS_WIRED`` is False the row says the block is drawn
    #    and not run, as a warning; it is still timed at the window's start
    #    when that resolves, so the story still sorts by it, and an
    #    unresolvable window keeps its own warning row. No site-derived figure
    #    enters the new sentences: the old "about N min" is a function of the
    #    latitude, and this answer is served to roles with no site view (#19).
    flats = out["flats"]
    if flats:
        wired = _dusk_flats_wired()
        adu = flats.get("adu_target")
        adu_txt = f"{int(_num(adu)):,}".replace(",", " ") if adu else "target"
        if flats["start_unix"] is not None and flats["end_unix"] is not None:
            if wired:
                mins = int(round(
                    (flats["end_unix"] - flats["start_unix"]) / 60.0))
                timed.append(row(
                    flats["start_unix"],
                    f"Flats window ({flats['window']}, about {mins} min): "
                    f"{str(flats['method']).lower()} flats, exposure solved "
                    f"to {adu_txt} ADU per filter", TONE_DIM))
            else:
                timed.append(row(
                    flats["start_unix"],
                    f"DUSK FLATS ({flats['window']}) is drawn but not run: "
                    f"{_FLATS_NOT_RUN}", TONE_WARN))
        elif wired:
            timed.append(row(
                dusk, f"Dusk flats are configured for \"{flats['window']}\" - "
                f"that window does not name two sun altitudes, so its clock "
                f"times are not resolved here", TONE_WARN))
        else:
            timed.append(row(
                dusk, f"DUSK FLATS ({flats['window']}) is drawn but not "
                f"run: {_FLATS_NOT_RUN}, and that window does not name two "
                f"sun altitudes, so its clock times are not resolved here",
                TONE_WARN))

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
                             f"Moon rises, {pct}% illuminated{near} - narrowband "
                             f"shrugs it off; broadband loses contrast",
                             TONE_FAINT))
        if moon.get("set_unix"):
            timed.append(row(moon["set_unix"],
                             "Moon sets - the rest of the night is dark sky",
                             TONE_FAINT))
        if not moon.get("rise_unix") and not moon.get("set_unix"):
            up = _num(moon.get("alt")) > 0.0
            timed.append(row(
                night["dark_start_unix"] or dusk,
                (f"Moon is up all night, {pct}% illuminated{near}" if up
                 else f"Moon stays down all night ({pct}% illuminated) - "
                      f"no moonglow in any of it"), TONE_FAINT))

    # 7. the meridian — only for targets that actually get a window. A pool
    # candidate that never clears its floor still crosses the meridian, and a
    # FLIP row for a target nobody images is a warning about nothing.
    for t in out["targets"]:
        if t.get("meridian_flip_unix") and t.get("window"):
            timed.append(row(t["meridian_flip_unix"],
                             f"{t['label']} crosses the meridian - engine flips, "
                             f"re-centers via plate solve, restarts guiding; "
                             f"worst case one frame lost", TONE_WARN))

    # 8. the rules, which have no hour. Read off the COMPILED instructions, not
    #    the graph: what fires tonight is what compiled, and a node wired to
    #    nothing compiles to nothing.
    whens = {str(i.get("when") or "") for i in (plan.get("instructions") or [])}
    if "on_clouds_in" in whens or "on_clouds_clear" in whens:
        # WHAT A HOLD DOES (#603 job A): it shoots darks, and only when a
        # CALIBRATION QUEUE on the canvas gives it a quota (`plan_extras`'
        # `cloud_hold_darks`). The row named "(black slot -> darks -> bias ->
        # flats-if-panel)" for every flow that holds, queue or none, and two
        # of those stages are not run. Read off the COMPILED automation, like
        # the rest of this tab.
        queue = ("the calibration queue takes darks only (bias and flats "
                 "are not run yet) · "
                 if automation.get("calibration_queue") else "")
        rules.append(row(None, f"IF clouds in → hold the loop · {queue}"
                               f"clean resume when it clears", TONE_WARN, "ANY"))
    if any(w.startswith("on_") and w not in
           {"on_clouds_in", "on_clouds_clear", "on_unsafe", "on_frame_graded"}
           for w in whens):
        rules.append(row(None, "IF the watchdog condition holds → its wired "
                               "actions fire at the next frame boundary",
                         TONE_WARN, "ANY"))
    if "on_unsafe" in whens:
        rules.append(row(None, "IF unsafe (or a stale reading) → abort, park, "
                               "warm, close - fail closed", TONE_BAD, "ANY"))

    # 9. the budget. A mosaic's row (it has `panels`) says the figures are
    #    every panel's, and what moving between them costs once measured. A
    #    banked figure says whose hours it holds, "for these targets" (#536),
    #    because the route counts only this flow's targets' reports
    #    (`flow_target_names`) and the archive's total is a different number.
    #
    #    WHAT HAPPENS TO THE REMAINDER is the plan's `resume_across_nights`
    #    (#195): only an explicit Off writes it False, and such a session is
    #    disarmed where its night ends, so "the run resumes the remainder
    #    next clear night" would be untrue of it. It reads the COMPILED plan,
    #    like every other row of this tab.
    resumes = plan.get("resume_across_nights") is not False
    remainder = ("the run resumes the remainder next clear night" if resumes
                 else _NO_LATER_RESUME)
    for b in out["budget"]:
        panels = hops = ""
        if "panels" in b:
            n = b["panels"]
            panels = f" across {n} panel{'' if n == 1 else 's'}"
            hops = (f", plus ≈{b['hop_h']:g} h moving between panels"
                    if b["hop_h"] is not None else
                    ", before the moves between panels, whose "
                    "duration has not been measured on this rig yet")
        if b.get("strategy") == "cycle":
            # S4 orchestrator ruling 5 (#338): a FILTER CYCLE's row. It has
            # no goal, and says so in words, where "0 h goal" would read as
            # a goal nobody can miss. Its hours are what its cycles OWE, not
            # "tonight adds": the eighth Example's cycles owe 16 h, more than
            # any night holds.
            n = b["cycles"]
            head = (f"{b['filter']} cycle: no integration goal (a FILTER "
                    f"CYCLE sets none)")
            owes = (f"its {n} cycle{' takes' if n == 1 else 's take'} "
                    f"≈{b['tonight_h']:g} h of exposure{panels}{hops}")
            rules.append(row(
                None,
                (f"{head}, {b['banked_h']:g} h captured in its filters "
                 f"{_FOR_THESE} - {owes}; {remainder}") if b["has_ledger"] else
                (f"{head} - {owes}. The session log was not read, so "
                 f"captured totals are unavailable"),
                TONE_GOOD, "TIME"))
            continue
        if b["has_ledger"]:
            rules.append(row(
                None,
                f"{b['filter']}: {b['banked_h']:g} h captured {_FOR_THESE} / "
                f"{b['goal_h']:g} h goal{panels} - tonight adds "
                f"≈{b['tonight_h']:g} h{hops}; {remainder}",
                TONE_GOOD, "TIME"))
        else:
            rules.append(row(
                None,
                f"{b['filter']}: {b['goal_h']:g} h goal{panels} - tonight adds "
                f"≈{b['tonight_h']:g} h{hops}. The session log was not read, "
                f"so captured totals are unavailable", TONE_GOOD,
                "TIME"))

    # 10. dawn. The report clause is only claimed when the GRAPH was supplied —
    # a session-report sink compiles to nothing, so a plan alone cannot know
    # whether the night leaves a ledger, and omitting it beats asserting it.
    closers = ""
    # NOT ", dome closes" (#192): whether the dome closes at dawn is a safety
    # SETTING (close_dome_when_done), not a fact of the compiled plan this
    # function reads, so this row cannot honestly claim it either way.
    if graph is not None and any(n.type == "report" for n in graph.nodes):
        closers += ", session report appended"
    # THIS ROW USED TO CLAIM A DAWN PARK UNCONDITIONALLY WHENEVER A DUSK
    # WINDOW WAS DRAWN (#191): whatever its Stop said, the story's closing
    # sentence still promised "Dawn: loop ends, mount parks, camera warms" -
    # the same broken promise the auto-resume tooltip made, per the owner's
    # 2026-09-24 answer on #189.
    # Now it says what the compiled ``stop_mode`` actually does.
    #
    # A FLOW WITH NO DUSK WINDOW AT ALL IS A DIFFERENT CASE, NOT "no stop
    # configured": ``compile_plan`` never writes a ``stop_mode`` key for one
    # (its schedule is just ``{"start_mode": "now"}``), and `plan_extras`
    # parks and warms when ANY run ends, dusk-bounded or not
    # (test_flows_night_ends_parked.py holds that claim against the plan's
    # own flag). So the absence of the KEY, not the value "none", is what
    # keeps this the old unconditional line - checked first, since
    # `str(sched.get("stop_mode") or "")` cannot tell "no key" from "none"
    # by itself.
    #
    # WITH A DUSK WINDOW: dawn parks as it always has, timed at ``dawn``,
    # which the sort below still reads as the last row (the true end of the
    # astronomical night, later than any other event this tab draws).
    # "Clock time" parks too (``park_when_done`` runs whenever the run ends,
    # not only at dawn), but AT ITS OWN CLOCK, which can fall earlier than
    # moonset or astronomical dark - so this row is NOT forced to be last;
    # the timeline is a chronological list, and an early stop is not "the
    # end" of it, only of the run. "None" - or a "Clock time" whose text did
    # not parse - gets the warning ``compile_plan`` already wrote, repeated
    # here (timed at ``dawn``, the instant the false promise used to name)
    # because this is where an operator reads the night, not the editor's
    # issue list.
    if "stop_mode" not in sched or stop_mode == "dawn":
        timed.append(row(dawn, f"Dawn: loop ends, mount parks, camera warms{closers}"))
    elif stop_mode == "time" and night["window_stop_unix"] is not None:
        timed.append(row(night["window_stop_unix"],
                         f"Run stops at the set clock time: mount parks, "
                         f"camera warms{closers}"))
    else:
        timed.append(row(
            dawn,
            "Dawn passes with no Stop configured: imaging continues into "
            "daylight and the mount is not parked - give the DUSK WINDOW a "
            "Stop", TONE_WARN))

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

    W7 FOLLOW-ON (WP-50, #561 class): the "above {floor}°", "never clears
    {floor}° in the dark tonight" and "No coordinates for" rows write a
    plain hyphen, not U+2014, the same copy rule #561 fixed for `_story`.
    The "Pool re-scores" row keeps its em-dash for now: it is not one of
    #561's six catalogued shapes and is left for its own fix
    (`test_w7_story_no_emdash.py`'s `_rows` docstring).
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
                "msg": (f"{names} never clears {floor:g}° in the dark tonight - "
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
                "msg": (f"{names} above {floor:g}° - slew, center, focus, "
                        f"guide, loop"), "tone": TONE_TEXT})
    if unplaced:
        rows.append({
            "t_unix": anchor, "label": "",
            "msg": (f"No coordinates for {', '.join(unplaced)} - not in the "
                    f"catalogue and none typed, so there is no curve for it "
                    f"and the scheduler cannot score it"),
            "tone": TONE_WARN})
    return rows
