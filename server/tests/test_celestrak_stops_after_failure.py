# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#635: CelesTrak's usage policy (celestrak.org/usage-policy.php) tells
automated clients to STOP after a non-200 response and involve a human, not
retry on a short fixed interval. ``elements.py`` keeps a failed fetch "due"
(``fetched_ts`` does not move -- see ``test_ephemeris_elements.py``), so
without a second mechanism ``EphemerisStore._tick`` would hit a down CelesTrak
every ``CHECK_INTERVAL_S`` (60s) forever. Every test here is about the backoff
that stops that: it engages on anything other than a clean success, it
doubles on each further consecutive failure up to a cap, it is cleared ONLY by
a fetch that actually writes a fresh envelope, it is published on the snapshot
an operator's screen reads, and an operator's own explicit refresh is never
held to it.

The ``_Boom`` client is the sibling suite's idiom, reused for the same reason:
proving the automatic poller did not even construct an HTTP client is stronger
than asserting about a call count.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from astrodeck.catalog.ephemeris import elements as el

WHEN = 1_789_012_800.0            # 2026-09-10T04:00:00Z, a fixed clock

#: A real ISS element set (also used by test_satellite_ephemeris.py), reused
#: here only as filler for a "the fetch worked" response -- nothing about
#: these specific numbers matters to any test in this file.
_L1 = "1 25544U 98067A   26253.14350205  .00005262  00000+0  10337-3 0  9999"
_L2 = "2 25544  51.6301 239.6211 0004991 124.3002 235.8459 15.49065630584920"


def _gp_json(ids) -> str:
    return json.dumps([{"OBJECT_NAME": f"SAT {int(i)}", "NORAD_CAT_ID": int(i),
                        "TLE_LINE1": _L1, "TLE_LINE2": _L2} for i in ids])


class _OkResp:
    """A response that parses -- ``raise_for_status`` does nothing."""

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHTTPStatusError(Exception):
    """Stands in for ``httpx.HTTPStatusError``: carries a
    ``.response.status_code`` the same way the real one does, and -- like the
    real one -- a message that WOULD carry the request URL if anything ever
    logged it directly (nothing in ``elements.py`` is allowed to)."""

    def __init__(self, status_code: int, url: str) -> None:
        self.response = _FakeResponse(status_code)
        super().__init__(f"Client error {status_code} for url {url}")


class _ErrorResp:
    def __init__(self, status_code: int, url: str) -> None:
        self._status = status_code
        self._url = url

    def raise_for_status(self) -> None:
        raise _FakeHTTPStatusError(self._status, self._url)


def _all_non200(status_code: int):
    """A client whose every CelesTrak leg (the group file and all three
    pinned catalogue numbers) answers with ``status_code`` -- the exact shape
    #635 describes: CelesTrak refusing a request, not a dropped connection."""

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return _ErrorResp(status_code, url)

    return _Client


class _Success:
    """Every CelesTrak leg succeeds: the group plus all three pinned ids, the
    ordinary-night shape. Used to prove the backoff is cleared only by a
    write, never by time alone."""

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if "GROUP=" in url:
            return _OkResp(_gp_json([11, 22]))
        catnr = int(url.split("CATNR=")[1].split("&")[0])
        return _OkResp(_gp_json([catnr]))


class _BoomError(BaseException):
    """Not an Exception: ``_fetch_one`` catches Exception on purpose, and a
    probe its own error handler can swallow proves nothing."""


class _Boom:
    """``httpx.AsyncClient`` stand-in whose CONSTRUCTION fails the test."""

    def __init__(self, *a, **kw) -> None:
        raise _BoomError(
            "an HTTP client was constructed despite an active #635 backoff")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An EphemerisStore whose two files live under tmp_path, with a FRESH
    comet cache already seeded.

    Every test here is about the satellite (CelesTrak) leg specifically. With
    no comet file at all, ``is_due(COMETS)`` is unconditionally True too, and
    ``_tick`` would reach for the Minor Planet Center on every test that
    exercises the poller -- a confound this module has no opinion about and
    the ``_Boom`` tests below would wrongly blame on the satellite backoff."""
    monkeypatch.setattr(el, "ELEMENTS_DIR", tmp_path / "ephemeris")
    monkeypatch.setattr(el, "SATELLITE_FILE",
                        tmp_path / "ephemeris" / "satellites.json")
    monkeypatch.setattr(el, "COMET_FILE", tmp_path / "ephemeris" / "comets.json")
    el.write_envelope(el.COMET_FILE, "mpc-cometels",
                      [{"id": "2P", "name": "2P/Encke", "q_au": 0.34,
                        "e": 0.85, "peri_deg": 187.0, "node_deg": 334.0,
                        "incl_deg": 11.3, "tp_tt_jd": 2461446.7,
                        "epoch_tt_jd": 2461293.5, "h_mag": 14.3,
                        "slope_g": 4.0}])
    return el.EphemerisStore()


# =========================================== the automatic retry must stop

def test_a_failed_fetch_blocks_the_next_automatic_tick(store, monkeypatch):
    """THE BUG #635 REPORTS, made concrete. A failed fetch leaves no envelope
    at all here, so ``is_due`` is unconditionally True on every later tick --
    it has nothing on disk to compare against. Without a backoff, ``_tick``
    would therefore reach for CelesTrak again a minute later, forever, which
    is exactly what CelesTrak's usage policy asks automated clients not to do.

    Named mutant (A): in ``_tick``, drop the ``until is not None and now <
    until`` guard (restore the plain ``await self.refresh(which)`` with no
    backoff check, the pre-#635 behaviour). Failing assertion: the final
    ``asyncio.run(store._tick())`` call raises ``_BoomError`` -- the tick
    reaches for CelesTrak again on the very next check despite the backoff,
    which is the bug this whole module exists to stop."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))
    asyncio.run(store._fetch_one(el.SATELLITES))
    assert el.is_due(el.SATELLITES) is True, (
        "the fixture itself is not due; this test would prove nothing")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    asyncio.run(store._tick())          # no _BoomError => the backoff held


def test_a_partial_outcome_also_arms_the_backoff(store, monkeypatch):
    """The group leg getting a non-200 is CelesTrak refusing a request, even
    though the three pinned ids came back -- the policy carves out no
    exception for "some of it worked", and a partial outcome leaves
    ``fetched_ts`` exactly where it was too (see
    ``test_a_failed_group_leg_never_shrinks_a_good_cache`` in
    test_ephemeris_elements.py), so the group leg alone would otherwise be
    retried every tick forever."""
    import httpx

    class _GroupFails(_all_non200(503)):
        async def get(self, url, **kw):
            if "GROUP=" in url:
                return await super().get(url, **kw)
            catnr = int(url.split("CATNR=")[1].split("&")[0])
            return _OkResp(_gp_json([catnr]))

    monkeypatch.setattr(httpx, "AsyncClient", _GroupFails)
    asyncio.run(store._fetch_one(el.SATELLITES))

    assert store.snapshot()["last_outcome"][el.SATELLITES] == "partial"
    assert el.SATELLITES in store.snapshot()["backoff_until"], (
        "a partial outcome (the group leg refused by CelesTrak) did not arm "
        "the backoff")


# ============================= the condition is published and logged

def test_the_backoff_is_published_and_logs_celestraks_response(
        store, monkeypatch, caplog):
    """"Publishes the condition": a status field a caller can read, and a log
    line that names what CelesTrak actually said -- never its URL.

    Named mutant (B): in ``_outcome_label``, always ``return
    type(e).__name__`` (drop the ``_UpstreamStatus`` branch). Failing
    assertion: ``assert "CelesTrak" in text and "503" in text`` -- the log
    would say ``_UpstreamStatus`` instead of ``CelesTrak responded 503``."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))
    monkeypatch.setattr(el.time, "time", lambda: WHEN)
    with caplog.at_level("WARNING"):
        asyncio.run(store._fetch_one(el.SATELLITES))

    until = store.snapshot(WHEN)["backoff_until"][el.SATELLITES]
    assert until == pytest.approx(WHEN + el.BACKOFF_FLOOR_S), (
        f"the first failure did not back off by the {el.BACKOFF_FLOOR_S}s "
        f"floor: next automatic attempt in {until - WHEN}s")

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CelesTrak" in text and "503" in text, (
        f"the log never named CelesTrak's response: {text!r}")
    assert "celestrak.org" not in text and "http" not in text, (
        "the backoff log carried the request URL")


# ===================================== doubling, capped, earned by a write

def test_consecutive_failures_double_the_backoff_to_the_cap(store, monkeypatch):
    """6h, 12h, 24h, 24h, ... -- never shorter, never past the cap.

    Named mutant (C): in ``_backoff_s``, ``return BACKOFF_FLOOR_S`` (drop the
    doubling). Failing assertion: the second failure's backoff equals
    ``BACKOFF_FLOOR_S`` (6h) instead of ``BACKOFF_FLOOR_S * 2`` (12h)."""
    import httpx

    t = [WHEN]
    monkeypatch.setattr(el.time, "time", lambda: t[0])
    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))

    expected = [el.BACKOFF_FLOOR_S, el.BACKOFF_FLOOR_S * 2, el.BACKOFF_CAP_S,
               el.BACKOFF_CAP_S]
    for n, delay in enumerate(expected, start=1):
        asyncio.run(store._fetch_one(el.SATELLITES))
        until = store.snapshot(t[0])["backoff_until"][el.SATELLITES]
        assert until - t[0] == pytest.approx(delay), (
            f"failure #{n}: expected a {delay}s backoff, got {until - t[0]}s")
        t[0] = until + 1.0           # the backoff has just elapsed


def test_the_backoff_resets_only_on_a_successful_fetch(store, monkeypatch):
    """EARNED, NOT GIVEN. Two failures climb the backoff to 12h; a clean
    success then clears it; the NEXT failure after that must start back at the
    6h floor -- if it did not, the reset would have to have come from
    something other than the write, which is the exact shape issue #635's own
    orchestrator ruling calls out (the relay client's reconnect backoff once
    went permanently to its ceiling the same way).

    Named mutant (D): in ``_fetch_one``, delete the two ``.pop(which, None)``
    calls in the ``if self._last[which] == "ok":`` branch (stop clearing the
    backoff on success). Failing assertion: ``el.SATELLITES not in
    store.snapshot(...)["backoff_until"]`` right after the clean fetch --
    the stale 12h backoff would still be sitting there."""
    import httpx

    t = [WHEN]
    monkeypatch.setattr(el.time, "time", lambda: t[0])

    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))
    asyncio.run(store._fetch_one(el.SATELLITES))              # failure #1: 6h
    t[0] += el.BACKOFF_FLOOR_S + 1.0
    asyncio.run(store._fetch_one(el.SATELLITES))              # failure #2: 12h
    assert store.snapshot(t[0])["backoff_until"][el.SATELLITES] - t[0] \
        == pytest.approx(el.BACKOFF_FLOOR_S * 2)

    t[0] += el.BACKOFF_FLOOR_S * 2 + 1.0
    monkeypatch.setattr(httpx, "AsyncClient", _Success)
    asyncio.run(store._fetch_one(el.SATELLITES))              # a clean success
    assert el.SATELLITES not in store.snapshot(t[0])["backoff_until"], (
        "a successful fetch left a stale backoff in place")

    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))
    asyncio.run(store._fetch_one(el.SATELLITES))              # failure #1 again
    assert store.snapshot(t[0])["backoff_until"][el.SATELLITES] - t[0] \
        == pytest.approx(el.BACKOFF_FLOOR_S), (
        "a failure after a success did not restart at the floor -- the "
        "backoff was earned back by something other than a write")


# =============================== an operator's own refresh is not gated

def test_an_explicit_refresh_bypasses_the_automatic_backoff(store, monkeypatch):
    """The backoff holds the AUTOMATIC poller only. An operator pressing
    Refresh in Sky settings calls ``store.refresh()``/``start_refresh()``
    directly, never through ``_tick``'s ``is_due``-plus-backoff gate -- the
    policy says involve a human, and this is the human doing exactly that."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _all_non200(503))
    asyncio.run(store._fetch_one(el.SATELLITES))
    assert el.SATELLITES in store.snapshot()["backoff_until"]

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    with pytest.raises(_BoomError):
        asyncio.run(store.refresh(el.SATELLITES))   # the operator's own path
