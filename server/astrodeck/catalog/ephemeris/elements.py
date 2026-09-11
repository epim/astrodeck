"""The orbital-element cache: two JSON files on disk, and the one poller that
keeps them fresh.

WHY A CACHE AT ALL, when every other catalog row in this package is a fixed
J2000 coordinate baked into the repo. A satellite's orbit is not a fact about
the sky, it is a measurement of an object that is being dragged by the
atmosphere and occasionally boosted by a thruster. The element set that
describes it decays: SGP4's mean position error passes roughly a kilometre a
day for a low-Earth orbit, so a three-day-old TLE puts the ISS a minute wrong
on a pass time, and a three-month-old one points at empty sky. A comet's
elements decay far more slowly but are republished weekly as new astrometry
arrives.

So the elements have to come off the network, and an observatory usually has
no network. That is the whole shape of this module, and it is the same shape
``catalog/survey_pack.py`` has for sky tiles and ``weather.py`` has for the
forecast:

* FETCH ONCE, KEEP ON DISK. Under ``CONFIG_DIR`` rather than ``CAPTURE_DIR``,
  because a self-update replaces the install tree and an operator who
  downloaded elements in town before driving to a dark site must still have
  them when they get there.
* A FAILED FETCH CHANGES NOTHING. The previous file is left byte-for-byte
  alone, ``fetched_ts`` does not move, and no empty envelope is ever written.
  Half the value of a cache is that a bad night cannot destroy a good one.
* AND A PARTIAL FETCH ADDS ONLY. The satellite fetch has four legs (the group
  plus three pinned ids) and can come back with three rows out of two hundred.
  Those three are MERGED into the envelope rather than written over it, and the
  file keeps its old ``fetched_ts`` -- see ``merge_satellite_rows``, which is
  where the finding that a lost group leg could empty a good cache is fixed.
* STALENESS IS DERIVED, NEVER STORED. It is ``now - fetched_ts`` measured at
  read time. A stored ``stale: true`` flag is a claim that goes wrong the
  moment the clock moves and nobody rewrites it.
* NOTHING GOES OUT UNTIL A TICK DECIDES A FILE IS DUE. The HTTP client is
  constructed inside the fetch, so a rig that never needs elements never opens
  a socket -- the ``_Boom`` invariant ``weather.py`` and ``survey_pack.py``
  are both held to.
* LOG THE OUTCOME, NEVER THE URL. Same rule as ``weather.py``: an exception
  type, never a query string.

WHAT THE FILES LOOK LIKE. One envelope each::

    {"fetched_ts": 1789012800.0, "source": "celestrak-visual", "rows": [...]}

written through ``persist.write_json_atomic`` (which keeps a ``.bak`` by
copying, so the primary is never momentarily absent) and read back through
``persist.read_json`` with the same ``.bak`` recovery ``LocationStore`` uses.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ...config import CONFIG_DIR

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ locations
#: Under CONFIG_DIR, not CAPTURE_DIR: this survives a self-update, and the
#: elements an operator fetched in town are the ones they need at the dark site.
ELEMENTS_DIR = CONFIG_DIR / "ephemeris"
SATELLITE_FILE = ELEMENTS_DIR / "satellites.json"
COMET_FILE = ELEMENTS_DIR / "comets.json"

# ------------------------------------------------------------------- upstreams
#: CelesTrak's curated bright-satellite group. See ``satellites.py`` for why
#: this list, and why no row carries a magnitude.
SATELLITE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=json"
SATELLITE_ID_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=json"
COMET_URL = "https://www.minorplanetcenter.net/iau/MPCORB/CometEls.txt"

#: CelesTrak ask that no client fetch a given file more than four times a day.
SATELLITE_REFRESH_S = 12 * 3600.0
#: The MPC republishes CometEls.txt weekly.
COMET_REFRESH_S = 7 * 86400.0

#: When the cache stops being something we will quote without a warning.
#: Three days, because SGP4's mean error passes ~1 km/day for a low orbit and a
#: kilometre along-track is about a minute of pass time.
SATELLITE_STALE_DAYS = 3.0
#: Sixty days. A comet's osculating elements drift slowly and the MPC's own
#: epoch is usually within a fortnight of publication.
COMET_STALE_DAYS = 60.0

#: The cap on how many of the group we keep, in CelesTrak's own order.
MAX_SATELLITES = 200

#: ISS, Tiangong/CSS, Hubble. Fetched individually and merged, so the three
#: objects a beginner will actually type are present even when the group fetch
#: failed or the cap cut them off.
PINNED_NORAD = (25544, 48274, 20580)

#: The same string ``weather.py`` sends. One identity for this app's outbound
#: requests, so an upstream that wants to rate-limit or block us can.
_USER_AGENT = "AstroDeck/0.1"
_TIMEOUT_S = 15.0

#: Poller cadence. Nothing here is due more often than twice a day, so a minute
#: of granularity is far finer than the decision needs; the tick exists so a
#: rig that comes online at 22:00 starts fetching within a minute rather than
#: at the next restart.
CHECK_INTERVAL_S = 60.0

SATELLITES = "satellites"
COMETS = "comets"
WHICH = (SATELLITES, COMETS)


class ElementsUnavailable(RuntimeError):
    """There are no usable elements for this kind of object.

    Raised instead of returning an empty list, so a caller cannot mistake "we
    have never downloaded these" for "there are none tonight"."""


class AlreadyFetching(RuntimeError):
    """A refresh for this kind is already running."""


# --------------------------------------------------------------- honest notes
#: What a caller is told when the cache has never been filled. Deliberately not
#: an empty list: an empty result that cannot be told apart from a real "there
#: is nothing there" is the failure ``SearchResult.notes`` exists to end.
NO_SATELLITES_NOTE = (
    "No satellite elements have been downloaded yet. AstroDeck fetches them "
    "from CelesTrak when it has a network connection; until then it cannot say "
    "where a satellite is, and will not guess.")

NO_COMETS_NOTE = (
    "No comet elements have been downloaded yet. AstroDeck fetches them from "
    "the Minor Planet Center when it has a network connection; until then it "
    "cannot say where a comet is, and will not guess.")


def stale_satellites_note(age_days: float) -> str:
    return (f"These satellite elements are {age_days:.0f} days old. A low-orbit "
            f"position drifts by roughly a kilometre a day, so a pass time this "
            f"old can be a minute out. Refresh them from Sky settings when the "
            f"rig is online.")


def stale_comets_note(age_days: float) -> str:
    return (f"These comet elements are {age_days:.0f} days old. The Minor "
            f"Planet Center republishes them weekly as new astrometry arrives, "
            f"so a position this old can be arcminutes out. Refresh them from "
            f"Sky settings when the rig is online.")


# ------------------------------------------------------------- the envelope IO

def _bak_path(path: Path) -> Path:
    """Where ``persist.write_json_atomic`` puts the previous copy."""
    return path.with_suffix(path.suffix + ".bak")


def _valid(raw: object) -> dict | None:
    """``raw`` as an envelope, or None when it is not one.

    Shape-checked rather than trusted: a file that parses as JSON and is not an
    envelope is exactly as unusable as one that does not parse, and the ``.bak``
    recovery below has to fire for both."""
    if not isinstance(raw, dict):
        return None
    rows = raw.get("rows")
    ts = raw.get("fetched_ts")
    if not isinstance(rows, list) or not isinstance(ts, (int, float)):
        return None
    if isinstance(ts, bool):            # bool is an int; a flag is not a clock
        return None
    return {"fetched_ts": float(ts),
            "source": str(raw.get("source") or ""),
            "rows": rows}


#: Parsed envelopes, keyed by path, each stamped with the ``(mtime_ns, size)``
#: it was parsed from.
#:
#: THE POLLER IS WHY. ``is_due`` asks about both files every ``CHECK_INTERVAL_S``
#: and ``cache_state`` asks again on every ``GET /api/ephemeris/status``, so a
#: 200-row satellite file was being read and JSON-parsed a few times a minute,
#: forever, on a board whose other job is guiding. The answer either function
#: wants out of it is one float.
#:
#: KEYED ON THE FILE'S OWN STAT, not on "we wrote it last": a file replaced by
#: anything at all -- this process, an operator dropping one in, the ``.bak``
#: recovery below -- changes mtime or size and misses the memo. ``write_envelope``
#: drops the entry explicitly as well, because Windows file timestamps move in
#: ~15 ms steps and two writes inside one step can land the same stat.
#:
#: No lock: the status route reads this from a worker thread while the poller
#: writes from the loop, and every operation on it is a single dict get/set/pop,
#: which is atomic. The worst a race can cost is one redundant parse.
_MEMO: dict[str, tuple[tuple[int, int], dict]] = {}


def _stat_key(path: Path) -> tuple[int, int] | None:
    """``(mtime_ns, size)``, or None when the file is not there to stat."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _forget(path: Path) -> None:
    """Drop the memo for ``path`` (and its ``.bak``), so the next read parses."""
    _MEMO.pop(str(path), None)
    _MEMO.pop(str(_bak_path(path)), None)


def _memoise(path: Path, env: dict) -> None:
    # Stat AFTER the read: a file rewritten while we were parsing it must not be
    # remembered under the stat it had before.
    key = _stat_key(path)
    if key is not None:
        _MEMO[str(path)] = (key, env)


def read_envelope(path: Path) -> dict | None:
    """The envelope at ``path``, recovering from ``<path>.bak`` when the primary
    is missing or unreadable, or None when neither is usable.

    Mirrors ``LocationStore._restore_from_bak``: the recovered copy is written
    back over the primary so the next read is a plain read, and the event bus
    says it happened. A silent recovery is a corruption nobody ever finds out
    about.

    The returned dict is a fresh top-level copy of the memoised one, so a caller
    that stamps a key onto it cannot edit what the next caller reads. The rows
    list inside it is shared and is READ-ONLY to callers."""
    from ...persist import read_json, write_json_atomic

    key = _stat_key(path)
    if key is not None:
        hit = _MEMO.get(str(path))
        if hit is not None and hit[0] == key:
            return dict(hit[1])

    try:
        env = _valid(read_json(path))
    except (FileNotFoundError, ValueError, OSError):
        env = None
    if env is not None:
        _memoise(path, env)
        return dict(env)
    try:
        env = _valid(read_json(_bak_path(path)))
    except (FileNotFoundError, ValueError, OSError):
        return None
    if env is None:
        return None
    from ...events import bus

    bus.log("warning", f"{path.name} restored from backup (.bak)", "config")
    try:
        write_json_atomic(path, env, backup=False)
    except OSError:                     # a read-only disk still gets the rows
        pass
    _memoise(path, env)
    return dict(env)


def write_envelope(path: Path, source: str, rows: list[dict],
                   fetched_ts: float | None = None) -> None:
    """Write an envelope atomically, keeping a ``.bak`` of the previous one.

    REFUSES AN EMPTY ROW LIST. Every caller reaches here after a fetch it
    believes succeeded, and a "successful" fetch that parsed to nothing is a
    parse failure wearing a 200. Overwriting a good cache with it would turn one
    bad response into a permanently blind rig."""
    from ...persist import ensure_dir, write_json_atomic

    if not rows:
        raise ElementsUnavailable(
            f"refusing to write an empty {path.name}: a fetch that produced no "
            f"rows is a failure, and a failure must not overwrite a good cache")
    ensure_dir(path.parent)
    write_json_atomic(path, {"fetched_ts": float(
        fetched_ts if fetched_ts is not None else time.time()),
        "source": source, "rows": rows})
    _forget(path)


def _norad_of(row: object) -> int | None:
    if not isinstance(row, dict):
        return None
    try:
        return int(row.get("norad_id"))
    except (TypeError, ValueError):
        return None


def merge_satellite_rows(path: Path, source: str, rows: list[dict]) -> None:
    """Fold a PARTIAL satellite fetch into the envelope already on disk.

    THE FAILURE THIS EXISTS FOR. ``fetch_satellites`` deliberately survives a
    failed group leg: the three pinned catalogue numbers are separate requests,
    so a night that loses CelesTrak's group file still comes back with the ISS.
    Those three rows are worth keeping and they are NOT a cache. Writing them
    through ``write_envelope`` would replace a good 200-row envelope with three
    rows AND stamp it with the current clock, so the rig would be blind to 197
    objects while ``cache_state`` reported the elements as downloaded minutes
    ago. Both halves of that are damage: the count, and the lie in the
    timestamp.

    So the fresh rows are merged into the ones already there (matched on
    ``norad_id``, the fetched copy winning, the existing order kept) and the
    envelope KEEPS ITS OLD ``fetched_ts`` -- because that is when the rows it is
    still mostly made of were actually fetched, and it is what makes ``stale``
    honest. The file therefore stays due, and the next tick tries the group
    again.

    Satellites only: comets are one file from one request, so there is no
    partial state to merge."""
    old = read_envelope(path)
    if old is None:
        # Nothing to protect. A partial set is better than no set at all, and
        # its fetched_ts really is now.
        write_envelope(path, source, rows)
        return
    fresh: dict[int, dict] = {}
    for r in rows:
        key = _norad_of(r)
        if key is not None:
            fresh[key] = r
    merged: list[dict] = []
    for r in old["rows"]:
        key = _norad_of(r)
        merged.append(fresh.pop(key, r) if key is not None else r)
    merged.extend(fresh.values())
    # Provenance is additive and de-duplicated: the group rows in here really
    # did come from the group fetch that succeeded days ago.
    parts = [p for p in str(old["source"] or "").split("+") if p]
    for p in str(source or "").split("+"):
        if p and p not in parts:
            parts.append(p)
    write_envelope(path, "+".join(parts) or source,
                   merged[:MAX_SATELLITES + len(PINNED_NORAD)],
                   old["fetched_ts"])


# ------------------------------------------------------------------ accessors

def _file_for(which: str) -> Path:
    # Read the module globals at CALL time, so a test that repoints
    # SATELLITE_FILE at a tmp_path is obeyed by every function here.
    return SATELLITE_FILE if which == SATELLITES else COMET_FILE


def _refresh_s(which: str) -> float:
    return SATELLITE_REFRESH_S if which == SATELLITES else COMET_REFRESH_S


def _stale_days(which: str) -> float:
    return SATELLITE_STALE_DAYS if which == SATELLITES else COMET_STALE_DAYS


def load(which: str) -> dict | None:
    """The cached envelope for ``which``, or None when there is none."""
    if which not in WHICH:
        raise KeyError(which)
    return read_envelope(_file_for(which))


def cache_state(which: str, now: float | None = None) -> dict:
    """The ``elements`` block every ephemeris payload carries.

    ``age_days`` is computed HERE, from ``fetched_ts``, on every read. It is
    never a stored field: a stored staleness flag is a claim that stops being
    true the moment the clock moves and nobody rewrites it."""
    now = time.time() if now is None else now
    env = load(which)
    if env is None:
        return {"which": which, "present": False, "source": None,
                "fetched_unix": None, "age_days": None, "stale": True,
                "count": 0,
                "note": NO_SATELLITES_NOTE if which == SATELLITES
                else NO_COMETS_NOTE}
    age = max(0.0, (now - env["fetched_ts"]) / 86400.0)
    stale = age > _stale_days(which)
    note = None
    if stale:
        note = (stale_satellites_note(age) if which == SATELLITES
                else stale_comets_note(age))
    return {"which": which, "present": True, "source": env["source"] or None,
            "fetched_unix": env["fetched_ts"], "age_days": round(age, 3),
            "stale": stale, "count": len(env["rows"]), "note": note}


def is_due(which: str, now: float | None = None) -> bool:
    """Should the poller fetch ``which`` on this tick?

    True when there is no cache at all, or when the one on disk is older than
    its refresh interval. Nothing else: a tick that answers False constructs no
    HTTP client and opens no socket."""
    now = time.time() if now is None else now
    env = load(which)
    if env is None:
        return True
    return (now - env["fetched_ts"]) >= _refresh_s(which)


# --------------------------------------------------------------- the fetchers
#
# These are the ONLY functions in the package that touch the network, and they
# are only ever called from a tick that has already decided a file is due or
# from the explicit refresh route. Each one PARSES before it returns, so a 200
# carrying an error page is a failure here rather than an empty cache later.

async def _get(client, url: str) -> str:
    r = await client.get(url, headers={"User-Agent": _USER_AGENT},
                         timeout=_TIMEOUT_S)
    r.raise_for_status()
    return r.text


def _satellite_rows_from_json(payload: object) -> list[dict]:
    """Normalise CelesTrak GP JSON into our row shape.

    TWO SHAPES, because CelesTrak's ``FORMAT=json`` is OMM (mean elements as
    named fields) and some deployments/proxies return records that also carry
    ``TLE_LINE1``/``TLE_LINE2``. A record with the two lines is used directly;
    one without keeps its OMM fields and ``satellites._satrec`` initialises
    from those instead. Either way the row records a NORAD id and a name, and
    a record with neither is dropped rather than stored half-formed."""
    if not isinstance(payload, list):
        raise ValueError("GP JSON is not a list")
    out: list[dict] = []
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("OBJECT_NAME") or rec.get("OBJECT_ID") or "").strip()
        try:
            norad = int(rec.get("NORAD_CAT_ID"))
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        l1 = rec.get("TLE_LINE1")
        l2 = rec.get("TLE_LINE2")
        row: dict = {"name": name, "norad_id": norad}
        if isinstance(l1, str) and isinstance(l2, str) and l1 and l2:
            row["line1"] = l1
            row["line2"] = l2
        elif rec.get("MEAN_MOTION") is not None:
            row["omm"] = {k: v for k, v in rec.items() if isinstance(k, str)}
        else:
            continue
        out.append(row)
    if not out:
        raise ValueError("GP JSON carried no usable records")
    return out


def satellite_rows_from_tle(text: str) -> list[dict]:
    """Parse a TLE file (the ``FORMAT=tle`` fallback).

    Accepts BOTH shapes CelesTrak serves: the 3-line form with a name line
    above each pair, and the bare 2-line form. A set whose name line is missing
    still yields a row -- named by its catalogue number, which is true, rather
    than dropped, which would silently shorten the list."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out: list[dict] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name, l1, l2 = "", ln, lines[i + 1]
            i += 2
        elif (i + 2 < len(lines) and lines[i + 1].startswith("1 ")
              and lines[i + 2].startswith("2 ")):
            name, l1, l2 = ln.strip(), lines[i + 1], lines[i + 2]
            i += 3
        else:
            i += 1
            continue
        try:
            norad = int(l2[2:7])
        except ValueError:
            continue
        out.append({"name": name or f"NORAD {norad}", "norad_id": norad,
                    "line1": l1, "line2": l2})
    if not out:
        raise ValueError("no TLE sets found")
    return out


async def fetch_satellites(client) -> tuple[str, list[dict], bool]:
    """``(source, rows, group_ok)`` for the satellite cache. Raises on a total
    failure.

    The group fetch and the three pinned ids are separate requests and separate
    failures. A group fetch that fails does not cost us the ISS; three pinned
    fetches that fail do not cost us the group. Only a total failure raises,
    and only then does the caller leave the old cache alone.

    ``group_ok`` IS THE THIRD VALUE BECAUSE THE ROW COUNT CANNOT SAY IT. A run
    that lost the group and kept the pinned ids returns three perfectly good
    rows, and three good rows written over a good 200-row cache is a rig that
    can no longer find 197 satellites -- with a fresh timestamp on the file, so
    nothing about it even looks wrong. The caller merges instead of overwriting
    when this is False (``merge_satellite_rows``)."""
    rows: list[dict] = []
    source_parts: list[str] = []
    group_ok = False
    try:
        text = await _get(client, SATELLITE_URL)
        try:
            import json

            rows = _satellite_rows_from_json(json.loads(text))
        except ValueError:
            rows = satellite_rows_from_tle(text)
        source_parts.append("celestrak-visual")
        group_ok = True
    except Exception as e:                       # noqa: BLE001 - outcome only
        log.warning("satellite group fetch failed: %s", type(e).__name__)
    rows = rows[:MAX_SATELLITES]

    have = {r["norad_id"] for r in rows}
    for catnr in PINNED_NORAD:
        if catnr in have:
            continue
        try:
            text = await _get(client, SATELLITE_ID_URL.format(catnr=catnr))
            import json

            try:
                one = _satellite_rows_from_json(json.loads(text))
            except ValueError:
                one = satellite_rows_from_tle(text)
            rows.extend(one)
            have.update(r["norad_id"] for r in one)
            source_parts.append(f"celestrak-{catnr}")
        except Exception as e:                   # noqa: BLE001 - outcome only
            log.warning("satellite %s fetch failed: %s", catnr, type(e).__name__)
    if not rows:
        raise ElementsUnavailable("no satellite elements could be fetched")
    return "+".join(source_parts) or "celestrak", rows, group_ok


async def fetch_comets(client) -> tuple[str, list[dict]]:
    """``(source, rows)`` for the comet cache. Raises on any failure."""
    from .comets import parse_comet_els

    text = await _get(client, COMET_URL)
    rows = parse_comet_els(text)
    if not rows:
        raise ElementsUnavailable("CometEls.txt carried no usable elements")
    return "mpc-cometels", rows


# ---------------------------------------------------------------- the service

class EphemerisStore:
    """One asyncio task that keeps the two element files fresh.

    Started with the app and stopped with it, exactly like ``DawnPark``. The
    tick is a no-op unless a file is older than its refresh interval, so a rig
    that never goes online costs one ``stat`` per file per minute and opens no
    socket at all."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._fetching: set[str] = set()
        self._jobs: set[asyncio.Task] = set()
        self._last: dict[str, str] = {}

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        jobs, self._jobs = list(self._jobs), set()
        for t in [task, *jobs]:
            if t is None:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL_S)
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 - a poller never dies
                log.warning("ephemeris tick failed: %s", type(e).__name__)

    async def _tick(self) -> None:
        for which in WHICH:
            if which in self._fetching:
                continue
            if not is_due(which):
                continue
            await self.refresh(which)

    # -- fetching ------------------------------------------------------------
    @property
    def fetching(self) -> frozenset[str]:
        return frozenset(self._fetching)

    def start_refresh(self, which: str) -> None:
        """Kick a background refresh. Raises :class:`AlreadyFetching` when one
        for the same kind is in flight -- the route turns that into a 409, so a
        button pressed twice does not become two requests to CelesTrak."""
        if which == "all":
            targets = list(WHICH)
        elif which in WHICH:
            targets = [which]
        else:
            raise KeyError(which)
        busy = [w for w in targets if w in self._fetching]
        if busy:
            raise AlreadyFetching(",".join(busy))
        for w in targets:
            self._fetching.add(w)
        task = asyncio.get_running_loop().create_task(self._run_refresh(targets))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_refresh(self, targets: list[str]) -> None:
        try:
            for w in targets:
                await self._fetch_one(w)
        finally:
            for w in targets:
                self._fetching.discard(w)

    async def refresh(self, which: str) -> None:
        """Fetch and write ``which`` now, in the caller's task."""
        if which in self._fetching:
            raise AlreadyFetching(which)
        self._fetching.add(which)
        try:
            await self._fetch_one(which)
        finally:
            self._fetching.discard(which)

    async def _fetch_one(self, which: str) -> None:
        """One fetch-and-write. NEVER raises: a failure leaves the cache exactly
        as it was and records the outcome, because the poller must survive a
        network that is down for a week."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                if which == SATELLITES:
                    source, rows, group_ok = await fetch_satellites(client)
                else:
                    source, rows = await fetch_comets(client)
                    group_ok = True     # one file, one request, no half-state
            if group_ok:
                write_envelope(_file_for(which), source, rows)
            else:
                # A PARTIAL SET NEVER REPLACES A WHOLE ONE. See
                # ``merge_satellite_rows``: the pinned rows are folded in and
                # the envelope keeps the timestamp of the group fetch that is
                # still most of it, so ``stale`` stays true to what is in there.
                merge_satellite_rows(_file_for(which), source, rows)
            self._last[which] = "ok" if group_ok else "partial"
        except asyncio.CancelledError:
            raise
        except Exception as e:          # noqa: BLE001 - outcome only, no URL
            # THE ONE ARM THAT MAKES THE CACHE SAFE. Everything above this line
            # either produced a complete set of rows or raised; nothing between
            # here and the write can partially update the file. So a failure
            # falls out here having touched nothing: the old envelope is intact,
            # byte for byte, and its fetched_ts has not moved.
            self._last[which] = type(e).__name__
            log.warning("ephemeris %s refresh failed: %s", which,
                        type(e).__name__)

    # -- status --------------------------------------------------------------
    def snapshot(self, now: float | None = None) -> dict:
        return {
            "satellites": cache_state(SATELLITES, now),
            "comets": cache_state(COMETS, now),
            "fetching": sorted(self._fetching),
            "last_outcome": dict(self._last),
        }


#: Module singleton, wired into the app lifespan beside ``dawn_park``.
ephemeris_store = EphemerisStore()
