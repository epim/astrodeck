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


class _Resp:
    """An ``httpx.Response`` stand-in: the text, and a ``raise_for_status`` that
    does not."""

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _gp_json(ids) -> str:
    """CelesTrak GP JSON for ``ids``, in the order given."""
    return json.dumps([{"OBJECT_NAME": f"SAT {int(i)}",
                        "NORAD_CAT_ID": int(i),
                        "TLE_LINE1": _ROWS[0]["line1"],
                        "TLE_LINE2": _ROWS[0]["line2"]} for i in ids])


def _legs(group_ids=None, pinned=True):
    """An ``httpx.AsyncClient`` class whose GROUP leg and PINNED legs succeed or
    fail INDEPENDENTLY.

    The satellite fetch is four requests -- the group file plus the three pinned
    catalogue numbers -- and the outages worth testing are the partial ones, so
    the fake is built per leg rather than as one on/off switch.
    ``group_ids=None`` fails the group; ``pinned=False`` fails all three."""
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if "GROUP=" in url:
                if group_ids is None:
                    raise OSError("group unavailable")
                return _Resp(_gp_json(group_ids))
            if not pinned:
                raise OSError("catalogue number unavailable")
            catnr = int(url.split("CATNR=")[1].split("&")[0])
            return _Resp(_gp_json([catnr]))

    return _Client


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


def test_the_poller_stats_the_file_rather_than_re_parsing_it(store,
                                                             monkeypatch):
    """``is_due`` asks about both files every minute and ``cache_state`` asks
    again on every status poll, and all either of them wants out of a 200-row
    JSON document is one float. Parsing it a few times a minute, forever, on a
    board whose other job is guiding, is a cost nobody asked for.

    AND THE MEMO HAS TO SEE A WRITE. That is the half that can go silently
    wrong: Windows file timestamps move in ~15 ms steps and a rewritten envelope
    of the same length lands the same ``(mtime, size)``, so a memo keyed on stat
    alone would keep serving the old rows. The second half of this test rewrites
    the file with a value that is byte-for-byte the same LENGTH and asks for it
    back."""
    import astrodeck.persist as persist

    reads: list = []
    real = persist.read_json

    def _counted(path):
        reads.append(str(path))
        return real(path)

    monkeypatch.setattr(persist, "read_json", _counted)

    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, WHEN)
    assert el.is_due(el.SATELLITES, WHEN) is False
    assert len(reads) == 1, f"the first read did not parse the file: {reads}"
    for _ in range(20):
        el.is_due(el.SATELLITES, WHEN)
        el.cache_state(el.SATELLITES, WHEN)
    assert len(reads) == 1, (
        f"40 poller reads re-parsed the element file {len(reads) - 1} times")

    later = WHEN + 100_000.0          # same digit count, so the same file size
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", _ROWS, later)
    assert el.cache_state(el.SATELLITES, later)["fetched_unix"] == later, (
        "a write was not seen: the memo served the envelope it replaced")


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
    """A group fetch that fails must not cost us the ISS. The three pinned
    catalogue numbers exist precisely so the objects a beginner types survive a
    partial outage. (The other direction is the test below, and what a partial
    outage must not do to an EXISTING cache is the one after that.)"""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _legs(group_ids=None))
    asyncio.run(store._fetch_one(el.SATELLITES))
    env = el.load(el.SATELLITES)
    assert env is not None
    got = {r["norad_id"] for r in env["rows"]}
    assert got == set(el.PINNED_NORAD), (
        f"the pinned ids did not survive a failed group fetch: {got}")


def test_the_pinned_ids_failing_does_not_cost_us_the_group(store, monkeypatch):
    """AND VICE VERSA, which nothing exercised: three pinned fetches that 404 or
    time out must leave the group file that arrived intact. The pinned legs are
    a top-up, and a top-up that fails is not a failed fetch."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient",
                        _legs(group_ids=[11, 22, 33], pinned=False))
    asyncio.run(store._fetch_one(el.SATELLITES))
    env = el.load(el.SATELLITES)
    assert env is not None
    assert [r["norad_id"] for r in env["rows"]] == [11, 22, 33], (
        "the group rows did not survive three failed pinned fetches")
    assert env["source"] == "celestrak-visual", (
        "a leg that failed must not be claimed as a source")
    assert store.snapshot(WHEN)["last_outcome"]["satellites"] == "ok"


def test_a_failed_group_leg_never_shrinks_a_good_cache(store, monkeypatch):
    """THE CACHE-DESTROYING SHAPE, and it needs no failure at all to look like a
    success.

    The group leg fails, the three pinned legs succeed, and the fetch returns
    three perfectly good rows. Written straight out, those three rows REPLACE a
    two-hundred-row envelope and get stamped with the current clock -- so the rig
    silently loses 197 satellites AND reports its elements as downloaded moments
    ago. One flaky night at CelesTrak would do it, and nothing on any screen
    would say so.

    So the fresh rows are merged in and the timestamp is left where it was: the
    envelope is still mostly the group fetch from days ago, and ``stale`` has to
    keep telling the truth about that."""
    seeded = [{"name": f"SAT {i}", "norad_id": i,
               "line1": _ROWS[0]["line1"], "line2": _ROWS[0]["line2"]}
              for i in range(1, 201)]
    el.write_envelope(el.SATELLITE_FILE, "celestrak-visual", seeded, WHEN)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _legs(group_ids=None))
    asyncio.run(store._fetch_one(el.SATELLITES))

    env = el.load(el.SATELLITES)
    ids = [r["norad_id"] for r in env["rows"]]
    assert len(ids) >= 200, (
        f"a failed group leg shrank a good 200-row cache to {len(ids)} rows")
    assert list(range(1, 201)) == ids[:200], (
        "the cached rows were reordered or replaced rather than merged into")
    assert set(el.PINNED_NORAD) <= set(ids), (
        "the pinned rows that DID arrive were dropped")
    assert env["fetched_ts"] == WHEN, (
        f"fetched_ts moved to {env['fetched_ts']} on a partial fetch -- the "
        f"cache would then read as fresh while carrying elements from before "
        f"the group leg failed")

    # And the honesty that timestamp buys: four days on, this is stale, because
    # 200 of its 203 rows really are four days old.
    late = WHEN + (el.SATELLITE_STALE_DAYS + 1.0) * 86400.0
    state = el.cache_state(el.SATELLITES, late)
    assert state["stale"] is True and state["count"] == len(ids)
    assert store.snapshot(WHEN)["last_outcome"]["satellites"] == "partial"


def test_the_group_is_capped_at_max_satellites(store, monkeypatch):
    """CelesTrak's own order, cut at the cap -- not a re-ranking of our own,
    because there is no magnitude to rank by (see the satellites module).

    GRADED ON THE FILE, not on the parser: the parser is deliberately uncapped
    (it returns everything the response carried) and the cap is applied by
    ``fetch_satellites`` on the way to disk, so a test that only measured the
    parser would pass with the cap deleted."""
    ids = list(range(1, el.MAX_SATELLITES + 51))
    assert len(el._satellite_rows_from_json(
        json.loads(_gp_json(ids)))) == len(ids), (
        "the parser is not where the cap lives")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient",
                        _legs(group_ids=ids, pinned=False))
    asyncio.run(store._fetch_one(el.SATELLITES))

    written = [r["norad_id"] for r in el.load(el.SATELLITES)["rows"]]
    assert len(written) == el.MAX_SATELLITES, (
        f"{len(written)} rows were written; the cap is {el.MAX_SATELLITES}")
    assert written == ids[:el.MAX_SATELLITES], (
        "the cap re-ordered the group; CelesTrak's order is the ranking")


def test_a_tle_format_fallback_parses_both_shapes():
    """``FORMAT=tle`` is the fallback when the GP JSON is unusable. Both the
    three-line form (with a name) and the bare two-line form parse."""
    three = ("ISS (ZARYA)\n" + _ROWS[0]["line1"] + "\n" + _ROWS[0]["line2"])
    rows = el.satellite_rows_from_tle(three)
    assert rows == [{"name": "ISS (ZARYA)", "norad_id": 25544,
                     "line1": _ROWS[0]["line1"], "line2": _ROWS[0]["line2"]}]
    bare = _ROWS[0]["line1"] + "\n" + _ROWS[0]["line2"]
    assert el.satellite_rows_from_tle(bare)[0]["name"] == "NORAD 25544"
