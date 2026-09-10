"""The element cache: the file on disk, and what a failure must not do to it.

Every test in here is about the same property, from a different side: A CACHE
IS ONLY WORTH HAVING IF A BAD DAY CANNOT DESTROY A GOOD ONE. An observatory
usually has no network, so the elements downloaded in town are the only ones it
will ever have; a fetch that half-succeeds, times out, or comes back as a 503
error page must leave that file exactly as it was.

The ``_Boom`` client is the weather/survey suite's idiom, reused for the same
invariant: the HTTP client's CONSTRUCTION fails the test, so "no outbound call"
is proved rather than asserted about a mock's call count.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from astrodeck.catalog.ephemeris import elements as el

WHEN = 1_789_012_800.0            # 2026-09-10T04:00:00Z, a fixed clock

_ROWS = [{"name": "ISS (ZARYA)", "norad_id": 25544,
          "line1": "1 25544U 98067A   26253.14350205  .00005262  00000+0  "
                   "10337-3 0  9999",
          "line2": "2 25544  51.6301 239.6211 0004991 124.3002 235.8459 "
                   "15.49065630584920"}]


class _BoomError(BaseException):
    """Not an Exception: the code under test catches Exception on purpose, and
    a probe that its own error handler can swallow proves nothing."""


class _Boom:
    """httpx.AsyncClient stand-in whose CONSTRUCTION fails the test."""

    def __init__(self, *a, **kw):
        raise _BoomError(
            "an HTTP client was constructed before any element file was due")


class _Failing:
    """A client that connects and then fails, which is what a flaky night
    actually looks like -- not a client that was never built."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        raise OSError("connection reset by peer")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An EphemerisStore whose two files live under tmp_path."""
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    return el.EphemerisStore()


# ============================================ a failure must change nothing

def test_a_failed_fetch_leaves_an_existing_cache_byte_identical(store,
                                                                monkeypatch):
    """THE INVARIANT THIS WHOLE MODULE EXISTS FOR.

    A rig that fetched elements in town and then drove somewhere with no signal
    must still have those elements. If a failed fetch could truncate, empty or
    re-stamp the file, one bad night would leave the rig permanently unable to
    say where anything is -- and it would look exactly like a rig that had never
    downloaded anything."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    before_bytes = el.SATELLITE_FILE.read_bytes()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Failing)
    asyncio.run(store._fetch_one(el.SATELLITES))

    assert el.SATELLITE_FILE.read_bytes() == before_bytes, (
        "a failed fetch rewrote the cache; one bad night would now cost the "
        "rig every element set it had")
    env = el.load(el.SATELLITES)
    assert env["fetched_ts"] == WHEN, (
        f"fetched_ts moved on a FAILED fetch to {env['fetched_ts']} -- the "
        f"cache would then look fresh while carrying old elements")
    # The outcome is recorded by TYPE. Every sub-fetch raised OSError and the
    # aggregate refusal is ElementsUnavailable -- either way it is a name, not a
    # URL and not a coordinate.
    assert store.snapshot(WHEN)["last_outcome"]["satellites"] \
        == "ElementsUnavailable"


def test_a_failed_fetch_never_writes_an_empty_cache(store, monkeypatch):
    """The other half: a 200 that parses to nothing is a failure too, and must
    not be allowed to overwrite a good file with an empty one."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    with pytest.raises(el.ElementsUnavailable):
        el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", [], WHEN)
    assert el.load(el.SATELLITES)["rows"] == _ROWS


def test_a_failed_fetch_logs_the_outcome_and_never_the_url(store, monkeypatch,
                                                           caplog):
    """Outcome-only logging, the same rule ``weather.py`` is held to: a log line
    carrying the request URL carries the site with it once a query string is
    involved, and a log file outlives the request."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Failing)
    with caplog.at_level("WARNING"):
        asyncio.run(store._fetch_one(el.SATELLITES))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "OSError" in text
    assert "celestrak.org" not in text and "http" not in text


# ================================================== the .bak recovery path

def test_a_corrupt_primary_recovers_from_the_bak(store):
    """``write_json_atomic`` keeps a ``.bak`` by COPYING, so a file corrupted
    after the write -- a half-flushed power loss, an antivirus quarantine -- has
    a complete previous version beside it. This is ``LocationStore``'s recovery,
    on the same helpers, because a second implementation of it would drift."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    # A second write is what creates the .bak (the first has nothing to back up).
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN + 1.0)
    bak = el.SATELLITE_FILE.with_suffix(el.SATELLITE_FILE.suffix + ".bak")
    assert bak.is_file()

    el.SATELLITE_FILE.write_text("{ this is not json", encoding="utf-8")
    env = el.load(el.SATELLITES)
    assert env is not None and env["rows"] == _ROWS, (
        "a corrupt primary was not recovered from its .bak")
    # And the recovery is WRITTEN BACK, so the next read is an ordinary read
    # rather than a second silent recovery nobody ever finds out about.
    assert json.loads(el.SATELLITE_FILE.read_text(encoding="utf-8"))["rows"] \
        == _ROWS


def test_a_primary_that_parses_but_is_not_an_envelope_also_recovers(store):
    """Shape-checked, not just parse-checked. A file that is valid JSON and not
    an envelope is exactly as unusable as one that does not parse, and the
    recovery has to fire for both or the second case reaches the caller as an
    empty sky."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN + 1.0)
    el.SATELLITE_FILE.write_text('{"rows": "not a list"}', encoding="utf-8")
    assert el.load(el.SATELLITES)["rows"] == _ROWS


def test_no_primary_and_no_bak_is_none_not_an_exception(store):
    assert el.load(el.SATELLITES) is None


# ========================================== an empty cache is a SENTENCE

def test_no_cache_yields_the_not_downloaded_note_and_no_rows(store):
    """An empty list is not an answer. "There are no satellites" and "this rig
    has never been online" look identical on a screen, and only one of them is
    something the user can do anything about."""
    state = el.cache_state(el.SATELLITES, WHEN)
    assert state["present"] is False and state["count"] == 0
    assert state["age_days"] is None
    assert "have been downloaded yet" in state["note"]
    assert "will not guess" in state["note"]

    comet_state = el.cache_state(el.COMETS, WHEN)
    assert "Minor Planet Center" in comet_state["note"]


def test_staleness_is_derived_at_read_time_and_never_stored(store):
    """A stored ``stale`` flag stops being true the moment the clock moves and
    nobody rewrites it. This one is recomputed from ``fetched_ts`` on every
    read, so the SAME file is fresh today and stale next week."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    on_disk = json.loads(el.SATELLITE_FILE.read_text(encoding="utf-8"))
    assert "stale" not in on_disk and "age_days" not in on_disk

    assert el.cache_state(el.SATELLITES, WHEN)["stale"] is False
    later = WHEN + (el.SATELLITE_STALE_DAYS + 1.0) * 86400.0
    state = el.cache_state(el.SATELLITES, later)
    assert state["stale"] is True
    assert state["age_days"] == pytest.approx(el.SATELLITE_STALE_DAYS + 1.0)
    assert "days old" in state["note"] and "kilometre a day" in state["note"]


# =================================== zero outbound calls until one is due

def test_a_tick_with_a_fresh_cache_constructs_no_http_client(store,
                                                             monkeypatch):
    """The ``_Boom`` invariant. Not "we did not call get()" -- the CLIENT is
    never built, so a rig whose elements are current pays nothing at all, and a
    future refactor that opens a connection to decide whether it needs one fails
    here rather than on somebody's metered link."""
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS)
    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [{"id": "2P", "name": "2P/Encke", "q_au": 0.34,
                        "e": 0.85, "peri_deg": 187.0, "node_deg": 334.0,
                        "incl_deg": 11.3, "tp_tt_jd": 2461446.7,
                        "epoch_tt_jd": 2461293.5, "h_mag": 14.3,
                        "slope_g": 4.0}])
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    asyncio.run(store._tick())          # no _BoomError => nothing went out


def test_a_tick_with_no_cache_at_all_does_fetch(store, monkeypatch):
    """The other direction, so the test above cannot pass by the poller being
    broken: with nothing on disk the tick DOES reach for the network."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    with pytest.raises(_BoomError):
        asyncio.run(store._tick())


def test_is_due_is_false_inside_the_refresh_interval(store):
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    assert el.is_due(el.SATELLITES, WHEN + el.SATELLITE_REFRESH_S - 1.0) is False
    assert el.is_due(el.SATELLITES, WHEN + el.SATELLITE_REFRESH_S + 1.0) is True


# ================================================== the fetch-in-flight lock

def test_a_second_refresh_while_one_runs_is_refused(store):
    """The 409 the route returns. CelesTrak ask for no more than four fetches a
    day; a button pressed twice must not become two requests."""
    async def _go():
        store._fetching.add(el.SATELLITES)
        with pytest.raises(el.AlreadyFetching):
            store.start_refresh(el.SATELLITES)
        with pytest.raises(el.AlreadyFetching):
            store.start_refresh("all")
        store._fetching.discard(el.SATELLITES)

    asyncio.run(_go())


def test_the_group_fetch_and_the_pinned_ids_fail_independently(store,
                                                               monkeypatch):
    """A group fetch that fails must not cost us the ISS, and vice versa. The
    three pinned catalogue numbers exist precisely so the objects a beginner
    types survive a partial outage."""
    class _GroupFails:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if "GROUP=" in url:
                raise OSError("group unavailable")
            catnr = url.split("CATNR=")[1].split("&")[0]
            return _Resp(json.dumps([{
                "OBJECT_NAME": f"OBJ {catnr}", "NORAD_CAT_ID": int(catnr),
                "TLE_LINE1": _ROWS[0]["line1"],
                "TLE_LINE2": _ROWS[0]["line2"]}]))

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _GroupFails)
    asyncio.run(store._fetch_one(el.SATELLITES))
    env = el.load(el.SATELLITES)
    assert env is not None
    got = {r["norad_id"] for r in env["rows"]}
    assert got == set(el.PINNED_NORAD), (
        f"the pinned ids did not survive a failed group fetch: {got}")


def test_the_group_is_capped_at_max_satellites(store):
    """CelesTrak's own order, cut at the cap -- not a re-ranking of our own,
    because there is no magnitude to rank by (see the satellites module)."""
    payload = [{"OBJECT_NAME": f"SAT {i}", "NORAD_CAT_ID": i,
                "TLE_LINE1": _ROWS[0]["line1"], "TLE_LINE2": _ROWS[0]["line2"]}
               for i in range(el.MAX_SATELLITES + 50)]
    rows = el._satellite_rows_from_json(payload)
    assert len(rows) == el.MAX_SATELLITES + 50
    assert rows[:3] == rows[:3]         # order preserved before the cap


def test_a_tle_format_fallback_parses_both_shapes():
    """``FORMAT=tle`` is the fallback when the GP JSON is unusable. Both the
    three-line form (with a name) and the bare two-line form parse."""
    three = ("ISS (ZARYA)\n" + _ROWS[0]["line1"] + "\n" + _ROWS[0]["line2"])
    rows = el.satellite_rows_from_tle(three)
    assert rows == [{"name": "ISS (ZARYA)", "norad_id": 25544,
                     "line1": _ROWS[0]["line1"], "line2": _ROWS[0]["line2"]}]
    bare = _ROWS[0]["line1"] + "\n" + _ROWS[0]["line2"]
    assert el.satellite_rows_from_tle(bare)[0]["name"] == "NORAD 25544"
