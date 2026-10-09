# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#862: Alpaca, NINA and ASIAIR syncs are read back (the #850 class).

#850 found the AM5 driver accepting a sync it never read back: the mount
answered as if it took three centring syncs of 2.2 to 2.8 degrees, took none,
and the run imaged the wrong field for hours. The other mount drivers trusted
their transport's success signal the same way. Each now reads the position
back through ``devices/sync_verify.py`` and raises ``SyncRefused`` (the
mount answered and its position did not move) or ``SyncUnverified`` (nobody
could tell), never a plain ``DeviceError``.

The drivers run for real: the Alpaca telescope over the REAL
``AlpacaConnection`` behind ``httpx.MockTransport`` (``FakeAlpacaMount``),
NINA over the repo's mock NINA app or a MockTransport NINA, the ASIAIR over
``test_asiair_backend.FakeAsiair``.

Every coordinate here is fictional. Each test names the mutant of production
code it was shown red under; mutants were applied to a byte copy of the file
and the file restored from that copy (sha256 checked).
"""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

import astrodeck.devices.backends.asiair_backend as ab
import astrodeck.hub as hub_module
from astrodeck.devices import sync_verify
from astrodeck.devices.alpaca import AlpacaConnection, AlpacaTelescope
from astrodeck.devices.backends import zwo_am5
from astrodeck.devices.base import SyncRefused, SyncUnverified
from astrodeck.devices.nina import (NINA_SYNC_FAILED_REASON, NinaClient,
                                    NinaTelescope)
from astrodeck.devices.sync_verify import (
    SYNC_NOT_MOVED_REASON,
    SYNC_NOT_SUPPORTED_REASON,
    SYNC_PARKED_REASON,
    SYNC_REFUSED_BY_DRIVER_REASON,
    SYNC_UNVERIFIED_LINK_BEFORE,
    SYNC_UNVERIFIED_LINK_DURING,
    SYNC_UNVERIFIED_READBACK,
    SYNC_UNVERIFIED_UNCLEAR,
    jnow_alternates,
    refused_message,
    unverified_message,
    verify_sync,
)

from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_850_hub_sync_refused import (_assert_surfaced_ok,
                                       _coordinate_spellings,
                                       _humanizer_rewrites, _use_solver)
from test_asiair_backend import FakeAsiair

#: A coordinate-shaped string the fakes put in every error text they own, so
#: a driver that quotes the error shows up in the leak checks.
LEAK = "07:23:41"


@pytest.fixture(autouse=True)
def _fast_readback(monkeypatch):
    """No real sleeps between reads unless a test is about the window."""
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRY_S", 0.0)


def _stub_precession(monkeypatch) -> None:
    """J2000 -> "JNOW" as an unmistakable shift, so the tests are about
    WHICH coordinates count and never reach astropy or IERS data."""
    monkeypatch.setattr(hub_module, "precess_j2000_to_jnow",
                        lambda ra, dec, when=None: ((ra + 0.25) % 24.0,
                                                    dec + 1.0))
    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000",
                        lambda ra, dec, when=None: ((ra - 0.25) % 24.0,
                                                    dec - 1.0))


# ------------------------------------------------------------ the Alpaca fake

def _alpaca_body(value=None, error_number=0, message=""):
    return {"Value": value, "ErrorNumber": error_number,
            "ErrorMessage": message, "ClientTransactionID": 0,
            "ServerTransactionID": 0}


class FakeAlpacaMount:
    """An Alpaca telescope endpoint behind ``httpx.MockTransport``.

    The REAL ``AlpacaConnection`` and ``AlpacaTelescope`` run on top of it,
    so ``_put``, ``_unwrap``, ``sync`` and ``get_position`` all run. Every
    request is recorded as ``(verb, method, params)``."""

    def __init__(self, *, ra: float = 10.0, dec: float = 40.0,
                 equatorial_system: int = 1, takes_sync: bool = True,
                 apply_after_reads: int = 0,
                 sync_error_number: int | None = None,
                 sync_http_status: int = 200, sync_body: str | None = None,
                 read_http_status: int = 200, read_nan: bool = False,
                 raise_on: dict | None = None, hang_on: set | None = None,
                 on_get: dict | None = None):
        self.ra, self.dec = ra, dec
        self.equatorial_system = equatorial_system
        self.takes_sync = takes_sync
        self.apply_after_reads = apply_after_reads
        self.sync_error_number = sync_error_number
        self.sync_http_status = sync_http_status
        self.sync_body = sync_body
        self.read_http_status = read_http_status
        self.read_nan = read_nan
        self.raise_on = raise_on or {}
        self.hang_on = hang_on or set()
        self.on_get = on_get or {}
        self.requests: list[tuple[str, str, dict]] = []
        self.slews: list[tuple[float, float]] = []
        self._pending: tuple[float, float] | None = None
        self._countdown = 0

    def gets_after(self, method: str, after: str) -> int:
        """How many GETs of ``method`` came after the first PUT of
        ``after``."""
        names = [(v, m) for v, m, _ in self.requests]
        start = names.index(("PUT", after))
        return sum(1 for v, m in names[start + 1:]
                   if v == "GET" and m == method)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        verb = request.method
        if verb == "GET":
            params = dict(request.url.params)
        else:
            params = {k: v[0] for k, v in
                      parse_qs(request.content.decode()).items()}
        self.requests.append((verb, method, params))
        if method in self.raise_on:
            raise self.raise_on[method]("fake transport failure",
                                        request=request)
        if verb == "GET" and method in self.hang_on:
            await asyncio.Event().wait()
        if verb == "GET" and method in self.on_get:
            self.on_get[method]()
        if verb == "PUT":
            return self._put(method, params)
        return self._get(method)

    def _put(self, method: str, params: dict) -> httpx.Response:
        if method == "synctocoordinates":
            if self.sync_body is not None:
                return httpx.Response(200, content=self.sync_body.encode(),
                                      headers={"content-type":
                                               "application/json"})
            if self.sync_http_status != 200:
                return httpx.Response(self.sync_http_status,
                                      text=f"refused at RA {LEAK}")
            if self.sync_error_number is not None:
                return httpx.Response(200, json=_alpaca_body(
                    error_number=self.sync_error_number,
                    message=f"RA {LEAK} Dec +41:00:00 is not allowed"))
            if self.takes_sync:
                self._pending = (float(params["RightAscension"]),
                                 float(params["Declination"]))
                self._countdown = self.apply_after_reads
                if not self._countdown:
                    self._apply()
            return httpx.Response(200, json=_alpaca_body())
        if method == "slewtocoordinatesasync":
            ra = float(params["RightAscension"])
            dec = float(params["Declination"])
            self.slews.append((ra, dec))
            self.ra, self.dec = ra, dec
            return httpx.Response(200, json=_alpaca_body())
        if method in ("tracking", "unpark", "abortslew", "connected"):
            return httpx.Response(200, json=_alpaca_body())
        return httpx.Response(200, json=_alpaca_body(
            error_number=0x400, message="not implemented"))

    def _apply(self) -> None:
        if self._pending is not None:
            self.ra, self.dec = self._pending
            self._pending = None

    def _get(self, method: str) -> httpx.Response:
        if method in ("rightascension", "declination"):
            if self.read_http_status != 200:
                return httpx.Response(self.read_http_status,
                                      text="read failed")
            if method == "rightascension" and self._pending is not None:
                if self._countdown <= 0:
                    self._apply()
                else:
                    self._countdown -= 1
            if self.read_nan:
                return httpx.Response(200, content=json.dumps(
                    _alpaca_body(float("nan"))).encode(),
                    headers={"content-type": "application/json"})
            value = self.ra if method == "rightascension" else self.dec
            return httpx.Response(200, json=_alpaca_body(value))
        if method == "equatorialsystem":
            return httpx.Response(200, json=_alpaca_body(
                self.equatorial_system))
        if method in ("slewing", "atpark"):
            return httpx.Response(200, json=_alpaca_body(False))
        if method == "tracking":
            return httpx.Response(200, json=_alpaca_body(True))
        return httpx.Response(200, json=_alpaca_body(
            error_number=0x400, message="not implemented"))


class _ClientClosed(RuntimeError):
    """What httpx raises for a request on a client that a reconnect has
    just closed (a RuntimeError, outside httpx.HTTPError). Takes the
    ``request`` keyword the fake's ``raise_on`` passes."""

    def __init__(self, message: str, request=None):
        super().__init__(message)


def alpaca_tel(fake: FakeAlpacaMount,
               name: str = "Fictional Alpaca Mount") -> AlpacaTelescope:
    """A REAL AlpacaTelescope over the REAL AlpacaConnection, talking to
    ``fake`` instead of the network."""
    conn = AlpacaConnection("alpaca.invalid", 11111)
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    tel = AlpacaTelescope(conn, 0, name)
    tel.connected = True
    return tel


def _no_leak(e: BaseException, *pairs: tuple[float, float]) -> None:
    for text in (str(e), getattr(e, "reason", "")):
        assert LEAK not in text, text
        for ra, dec in pairs:
            for spelling in _coordinate_spellings(ra, dec):
                assert spelling not in text, (spelling, text)


# ------------------------------------------------------------------- Alpaca

async def test_alpaca_a_sync_the_mount_takes_is_read_back_and_returns():
    """MUTANT N1 "no read-back" (the ``await verify_sync(...)`` line in
    ``AlpacaTelescope.sync`` deleted): RED (observed), ``assert 0 >= 1`` on
    the ordering assertion."""
    fake = FakeAlpacaMount()
    tel = alpaca_tel(fake)
    assert await tel.sync(10.0, 42.5) is None
    assert fake.gets_after("rightascension", "synctocoordinates") >= 1


async def test_alpaca_a_sync_the_mount_ignores_is_refused_with_its_residual():
    """MUTANT N1 (verify line deleted): RED (observed), DID NOT RAISE.
    MUTANT N2 "a 10 deg tolerance" (``SYNC_VERIFY_DEG = 10.0``): RED
    (observed), DID NOT RAISE."""
    fake = FakeAlpacaMount(takes_sync=False)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncRefused) as info:
        await tel.sync(10.0, 42.5)
    e = info.value
    assert e.code == "OK"
    assert e.reason == SYNC_NOT_MOVED_REASON
    assert e.residual_deg == pytest.approx(2.5, abs=0.01)
    _no_leak(e, (10.0, 40.0), (10.0, 42.5))


async def test_alpaca_a_report_that_moves_late_is_not_a_refusal():
    """The fourth read after the sync is the first to see the new position.

    MUTANT N3 "one read decides" (``range(SYNC_READBACK_RETRIES + 1)`` ->
    ``range(1)``): RED (observed), SyncRefused."""
    fake = FakeAlpacaMount(apply_after_reads=3)
    tel = alpaca_tel(fake)
    assert await tel.sync(10.0, 42.5) is None
    assert fake.gets_after("rightascension", "synctocoordinates") == 4


async def test_alpaca_a_report_that_never_moves_costs_exactly_the_window():
    """Six reads, then the refusal; not one more.

    MUTANT N4 "one read more" (``range(SYNC_READBACK_RETRIES + 2)``): RED
    (observed), ``assert 7 == 6``."""
    fake = FakeAlpacaMount(takes_sync=False)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncRefused):
        await tel.sync(10.0, 42.5)
    assert fake.gets_after("rightascension", "synctocoordinates") == 6


@pytest.mark.parametrize("error_number,http_status,reason,shown", [
    (0x408, 200, SYNC_PARKED_REASON, "0x408"),
    (0x40B, 200, SYNC_REFUSED_BY_DRIVER_REASON, "0x40B"),
    (0x400, 200, SYNC_REFUSED_BY_DRIVER_REASON, "0x400"),
    (-2147220478, 200, SYNC_REFUSED_BY_DRIVER_REASON, "-2147220478"),
    (None, 400, SYNC_REFUSED_BY_DRIVER_REASON, None),
])
async def test_alpaca_an_ascom_error_is_a_refusal_in_words(
        bus_lines, error_number, http_status, reason, shown):
    """A positively known refusal: an ASCOM ErrorNumber, or HTTP 4xx. One
    best-effort read gives the residual; the driver's ErrorMessage is never
    quoted, and the number gets its own info line.

    MUTANT N5 "an ASCOM error reads as unverified" (the refusal condition in
    ``sync`` made ``False``): RED (observed), SyncUnverified on every case.
    MUTANT N6 "ErrorMessage quoted" (``str(e)`` of the AlpacaReplyError
    appended to the refusal message): RED (observed) on the leak assertion.
    MUTANT N7 "0x400 is not supported" (a ``0x400 ->
    SYNC_NOT_SUPPORTED_REASON`` arm added): RED (observed) on the 0x400 case.
    MUTANT G3 "a negative number in hex" (``f"0x{error_number:X}"`` for
    every number): RED (observed) on the negative case, ``0x-7FFBFDFE``.
    """
    if error_number is not None:
        fake = FakeAlpacaMount(sync_error_number=error_number)
    else:
        fake = FakeAlpacaMount(sync_http_status=http_status)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncRefused) as info:
        await tel.sync(10.0, 42.5)
    e = info.value
    assert e.code == "error"
    assert e.reason == reason
    assert e.residual_deg == pytest.approx(2.5, abs=0.01)
    _no_leak(e, (10.0, 40.0), (10.0, 42.5))
    for _, m, _ in bus_lines:
        assert LEAK not in m, m
    numbered = [m for _, m, _ in bus_lines if "ASCOM error" in m]
    if shown is not None:
        assert numbered == [
            f"Fictional Alpaca Mount: the driver answered the sync with "
            f"ASCOM error {shown}"], numbered
    else:
        assert numbered == [], numbered


@pytest.mark.parametrize("how", ["http_500", "hang"])
async def test_alpaca_a_refusal_whose_position_cannot_be_read_has_no_residual(
        monkeypatch, how):
    """MUTANT N5b "a failed residual read escapes" (the first ``except`` in
    ``refused_residual_deg`` deleted): RED (observed) on the 500 case,
    AlpacaReplyError.
    MUTANT N5c "the residual read is unbounded" (the ``wait_for`` in
    ``refused_residual_deg`` removed): RED (observed) on the hang case, the
    outer TimeoutError."""
    if how == "http_500":
        fake = FakeAlpacaMount(sync_error_number=0x408, read_http_status=500)
    else:
        monkeypatch.setattr(sync_verify, "SYNC_READ_TIMEOUT_S", 0.05)
        fake = FakeAlpacaMount(sync_error_number=0x408,
                               hang_on={"rightascension"})
    tel = alpaca_tel(fake)
    with pytest.raises(SyncRefused) as info:
        await asyncio.wait_for(tel.sync(10.0, 42.5), 5.0)
    assert info.value.reason == SYNC_PARKED_REASON
    assert info.value.residual_deg is None
    assert "deg off" not in str(info.value)


@pytest.mark.parametrize("knobs,reason", [
    ({"raise_on": {"synctocoordinates": httpx.ConnectError}},
     SYNC_UNVERIFIED_LINK_DURING),
    ({"sync_http_status": 500}, SYNC_UNVERIFIED_UNCLEAR),
    ({"sync_body": "<html>bad gateway</html>"}, SYNC_UNVERIFIED_UNCLEAR),
    ({"sync_body": '{"ErrorNumber": "abc", "ErrorMessage": "RA 07:23:41"}'},
     SYNC_UNVERIFIED_UNCLEAR),
    ({"sync_body": '{"ErrorNumber": true, "ErrorMessage": "RA 07:23:41"}'},
     SYNC_UNVERIFIED_UNCLEAR),
    ({"sync_body": "[1, 2]"}, SYNC_UNVERIFIED_UNCLEAR),
    ({"raise_on": {"synctocoordinates": _ClientClosed}},
     SYNC_UNVERIFIED_UNCLEAR),
], ids=["connect_error", "http_500", "not_json", "error_number_abc",
        "error_number_true", "list_body", "client_closed"])
async def test_alpaca_a_sync_call_that_fails_without_a_refusal_is_unverified(
        bus_lines, knobs, reason):
    """No answer, or an answer that is not a known refusal: unverified (the
    conservative arm: the resume ladder slews on a small refusal and holds
    on an unverified sync). No read-back is attempted, and nothing of the
    transport's text rides along in ``__context__``.

    MUTANT N8 "transport errors escape" (the ``except (httpx.HTTPError,
    OSError)`` arm deleted): RED (observed); since fix round 1's catch-all
    the ConnectError reads as "unclear" in place of "the link failed".
    MUTANT N8b "5xx is a refusal" (``400 <= e.http_status < 500`` ->
    ``e.http_status >= 400``): RED (observed) on the 500 case, SyncRefused.
    MUTANT N8c "an unreadable ErrorNumber is a refusal" (``_error_number``
    returns -1 for a non-number): RED (observed) on the "abc" case,
    SyncRefused.
    MUTANT N9 "raised inside the handler" (the link raise moved into the
    ``except (httpx.HTTPError, OSError)`` arm): RED (observed) on
    ``__context__`` for the ConnectError case.
    MUTANT G19 "a boolean ErrorNumber is a number" (the ``isinstance(raw,
    bool)`` guard in ``_error_number`` deleted): RED (observed) on
    error_number_true, SyncRefused.
    MUTANT N29 "no catch-all" (the final ``except Exception`` arm in
    ``AlpacaTelescope.sync`` deleted): RED (observed) on list_body
    (AttributeError) and client_closed (RuntimeError)."""
    fake = FakeAlpacaMount(**knobs)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncUnverified) as info:
        await tel.sync(10.0, 42.5)
    e = info.value
    assert e.code == ""
    assert e.reason == reason
    assert e.__context__ is None and e.__cause__ is None
    assert fake.gets_after("rightascension", "synctocoordinates") == 0
    assert not any("ASCOM error" in m for _, m, _ in bus_lines), bus_lines
    _no_leak(e, (10.0, 40.0), (10.0, 42.5))


async def test_alpaca_a_read_back_that_never_answers_is_unverified():
    """MUTANT N10 "a failed last read reads as refused" (the ``last_failed
    is not None`` raise deleted): RED (observed), a SyncRefused with no
    residual in place of the SyncUnverified."""
    fake = FakeAlpacaMount(read_http_status=500)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncUnverified) as info:
        await tel.sync(10.0, 42.5)
    e = info.value
    assert e.code == "OK"
    assert e.reason == SYNC_UNVERIFIED_READBACK
    assert str(e).startswith("sync not confirmed: " + SYNC_UNVERIFIED_READBACK)
    assert str(e).endswith("; the last read got no usable answer)")


async def test_alpaca_a_non_finite_read_back_is_unverified():
    """MUTANT N11 "no finite check" (the ``isfinite`` test in ``_parse``
    deleted, so ``angular_sep_deg``'s ValueError escapes): RED (observed),
    ValueError."""
    fake = FakeAlpacaMount(read_nan=True)
    tel = alpaca_tel(fake)
    with pytest.raises(SyncUnverified) as info:
        await tel.sync(10.0, 42.5)
    assert str(info.value).endswith(
        "; the last read got a position that was not finite)")


# --------------------------------------------------------------------- NINA

def _mock_nina():
    from tools.mock_nina import create_mock_nina
    app, state = create_mock_nina()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://nina.test")
    tel = NinaTelescope(NinaClient("nina.test", 1888, http=client),
                        "Fictional Bridge Mount")
    tel.connected = True
    return tel, state, client


async def test_nina_a_sync_is_read_back_from_a_fresh_mount_info(monkeypatch):
    """``get_position`` first primes the 0.4 s info cache with the pre-sync
    position; the read-back must not read that cache.

    MUTANT N12 "the read-back uses the cached info" (``force=True`` ->
    ``force=False`` in ``_read_position_strict``): RED (observed),
    SyncRefused."""
    _stub_precession(monkeypatch)
    tel, state, client = _mock_nina()
    try:
        state.rig.ra_hours, state.rig.dec_deg = 9.0, 30.0
        assert await tel.get_position() == (9.0, 30.0)
        assert await tel.sync(10.0, 41.0) is None
        assert (state.rig.ra_hours, state.rig.dec_deg) == (10.0, 41.0)
    finally:
        await client.aclose()


async def test_nina_a_sync_the_mount_ignores_is_refused(monkeypatch):
    """MUTANT N13 "no read-back" (the ``verify_sync`` line in
    ``NinaTelescope.sync`` deleted): RED (observed), DID NOT RAISE."""
    _stub_precession(monkeypatch)
    tel, state, client = _mock_nina()
    try:
        state.rig.ra_hours, state.rig.dec_deg = 9.0, 30.0

        async def ignores(ra_hours, dec_deg):
            return None

        monkeypatch.setattr(state.dev["telescope"], "sync", ignores)
        with pytest.raises(SyncRefused) as info:
            await tel.sync(10.0, 41.0)
        assert info.value.code == "OK"
        assert info.value.reason == SYNC_NOT_MOVED_REASON
    finally:
        await client.aclose()


async def test_nina_a_report_in_the_mounts_jnow_frame_counts_as_taken(
        monkeypatch):
    """NINA transforms a sync to the mount's epoch and reports in it.

    MUTANT N14 "no alternate frame" (``alternates=None`` in
    ``NinaTelescope.sync``'s ``verify_sync`` call): RED (observed),
    SyncRefused."""
    _stub_precession(monkeypatch)
    tel, state, client = _mock_nina()
    try:
        rig = state.rig

        async def jnow_sync(ra_hours, dec_deg):
            rig.ra_hours, rig.dec_deg = ra_hours + 0.25, dec_deg + 1.0

        monkeypatch.setattr(state.dev["telescope"], "sync", jnow_sync)
        assert await tel.sync(10.0, 41.0) is None
    finally:
        await client.aclose()


async def test_nina_a_report_in_neither_frame_is_refused(monkeypatch):
    """MUTANT N15 "an alternate match ignores the tolerance" (the alternate
    branch of ``_residual`` returns 0.0): RED (observed), DID NOT RAISE."""
    _stub_precession(monkeypatch)
    tel, state, client = _mock_nina()
    try:
        rig = state.rig

        async def half_degree_off(ra_hours, dec_deg):
            rig.ra_hours, rig.dec_deg = ra_hours, dec_deg + 0.5

        monkeypatch.setattr(state.dev["telescope"], "sync", half_degree_off)
        with pytest.raises(SyncRefused) as info:
            await tel.sync(10.0, 41.0)
        assert info.value.residual_deg == pytest.approx(0.5, abs=0.01)
    finally:
        await client.aclose()


def _transport_nina(sync_answer):
    """A NINA whose mount info reports (10.0, 40.0) and whose sync answers
    with ``sync_answer(request)``."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/equipment/mount/info"):
            return httpx.Response(200, json={
                "Success": True, "Response": {
                    "RightAscension": 10.0, "Declination": 40.0,
                    "Connected": True}})
        if request.url.path.endswith("/equipment/mount/sync"):
            return sync_answer(request)
        return httpx.Response(404, text="no such route")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tel = NinaTelescope(NinaClient("nina.invalid", 1888, http=client),
                        "Fictional Bridge Mount")
    tel.connected = True
    return tel, client


def _answers(kind):
    def answer(request):
        if kind == "success_false":
            return httpx.Response(200, json={
                "Success": False, "Error": f"Mount refused RA {LEAK}"})
        if kind == "http_409":
            return httpx.Response(409, text=f"conflict at RA {LEAK}")
        if kind == "http_500":
            return httpx.Response(500, text=f"server error RA {LEAK}")
        if kind == "not_json":
            return httpx.Response(200, text="<html>proxy page</html>")
        raise httpx.ConnectError("fake transport failure", request=request)
    return answer


@pytest.mark.parametrize("kind,exc,code,reason", [
    ("success_false", SyncRefused, "error", NINA_SYNC_FAILED_REASON),
    ("http_409", SyncRefused, "error", NINA_SYNC_FAILED_REASON),
    ("http_500", SyncUnverified, "", SYNC_UNVERIFIED_UNCLEAR),
    ("not_json", SyncUnverified, "", SYNC_UNVERIFIED_UNCLEAR),
    ("connect_error", SyncUnverified, "", SYNC_UNVERIFIED_LINK_DURING),
])
async def test_nina_only_a_known_refusal_is_a_refusal(monkeypatch, kind, exc,
                                                      code, reason):
    """MUTANT N16 "transport errors escape" (the ``except Exception`` arm
    in ``NinaTelescope.sync`` deleted): RED (observed) on connect_error,
    httpx.ConnectError.
    MUTANT N16b "every NINA error is a refusal" (``except DeviceError: kind
    = "refused"`` in place of the NinaReplyError and DeviceError arms): RED
    (observed) on http_500, the first of the two cases it breaks.
    MUTANT N16c "``NinaClient.get`` raises a plain DeviceError" (the two
    NinaReplyError raises back to DeviceError): RED (observed) on
    success_false, the first of the two cases it breaks, SyncUnverified."""
    _stub_precession(monkeypatch)
    tel, client = _transport_nina(_answers(kind))
    try:
        with pytest.raises(exc) as info:
            await tel.sync(10.0, 42.5)
        e = info.value
        assert type(e) is exc
        assert e.code == code
        assert e.reason == reason
        if exc is SyncRefused:
            assert e.residual_deg == pytest.approx(2.5, abs=0.01)
        assert e.__context__ is None and e.__cause__ is None
        _no_leak(e, (10.0, 40.0), (10.0, 42.5))
    finally:
        await client.aclose()


# ------------------------------------------------------------------- ASIAIR

def _asiair_fake(monkeypatch, **kw) -> FakeAsiair:
    f = FakeAsiair(**kw)
    monkeypatch.setattr(ab, "make_client", lambda host, timeout=10.0: f)
    return f


async def _asiair_tel(fake: FakeAsiair):
    from types import SimpleNamespace
    conn = SimpleNamespace(host="asiair.invalid", port=4700, extra={})
    session = await ab.AsiairBackend().open(conn)
    tel = await session.get_device("telescope", conn)
    return session, tel


def _moving_sync(fake: FakeAsiair):
    def sync(ra, dec):
        fake._rec("mount.sync", ra, dec)
        fake.raw["RA"], fake.raw["Dec"] = float(ra), float(dec)
    return sync


async def test_asiair_a_sync_the_box_takes_is_read_back(monkeypatch):
    """MUTANT N17 "no read-back" (the ASIAIR ``verify_sync`` line deleted):
    RED (observed) on the label ordering."""
    _stub_precession(monkeypatch)
    fake = _asiair_fake(monkeypatch)
    monkeypatch.setattr(fake.mount, "sync", _moving_sync(fake))
    session, tel = await _asiair_tel(fake)
    try:
        assert await tel.sync(5.9, 35.0) is None
        labels = fake.labels
        last_sync = max(i for i, x in enumerate(labels) if x == "mount.sync")
        assert "mount.info" in labels[last_sync + 1:], labels
    finally:
        await session.close()


async def test_asiair_a_sync_the_mount_ignores_is_refused(monkeypatch):
    """MUTANT N17 (verify line deleted): RED (observed), DID NOT RAISE."""
    _stub_precession(monkeypatch)
    fake = _asiair_fake(monkeypatch)
    session, tel = await _asiair_tel(fake)
    try:
        with pytest.raises(SyncRefused) as info:
            await tel.sync(5.9, 35.0)
        assert info.value.code == "OK"
        assert info.value.reason == ab.ASIAIR_NOT_MOVED_REASON
        assert info.value.residual_deg == pytest.approx(2.5, abs=0.01)
    finally:
        await session.close()


async def test_asiair_a_missing_position_is_not_a_position(monkeypatch):
    """MUTANT N18 "read back through get_position"
    (``self._read_position_strict`` -> ``self.get_position`` in the
    ``verify_sync`` call): RED (observed), DID NOT RAISE (the 0.0 default
    "matches" a sync to (0, 0))."""
    _stub_precession(monkeypatch)
    fake = _asiair_fake(monkeypatch)

    def loses_position(ra, dec):
        fake._rec("mount.sync", ra, dec)
        fake.raw.pop("RA", None)
        fake.raw.pop("Dec", None)

    monkeypatch.setattr(fake.mount, "sync", loses_position)
    session, tel = await _asiair_tel(fake)
    try:
        with pytest.raises(SyncUnverified) as info:
            await tel.sync(0.0, 0.0)
        assert info.value.reason == SYNC_UNVERIFIED_READBACK
    finally:
        await session.close()


@pytest.mark.parametrize("case", ["busy", "sync_busy", "no_sync_cap",
                                  "idle_check_link", "sync_link",
                                  "sync_unclear"])
async def test_asiair_busy_dropped_and_unclear_boxes_are_told_apart(
        monkeypatch, case):
    """MUTANT N19 "busy reads as a link failure" (the ``_is_busy`` test on
    the idle check's cause made False): RED (observed) on busy.
    MUTANT N19b "a refusal reads no residual" (``_sync_refused`` passes
    ``residual = None`` instead of reading): RED (observed) on busy, the
    first case (the mutant run stops at the first failure; no_sync_cap
    asserts the same residual).
    MUTANT N19c "an unknown libasi error is a refusal" (the sync call's
    non-OSError arm raising SyncRefused(code="error")): RED (observed) on
    sync_unclear.
    MUTANT N19d "a failed idle check is a refusal" (the non-busy idle-check
    arm raising the busy refusal): RED (observed) on idle_check_link.
    MUTANT G22 "busy during the sync call is unclear" (the ``if
    _is_busy(cause): kind = "busy"`` branch of the sync call's ``except``
    removed): RED (observed) on sync_busy, SyncUnverified."""
    _stub_precession(monkeypatch)
    busy_on = {"busy": {"check_idle"}, "sync_busy": {"mount.sync"}}.get(case)
    fake = _asiair_fake(monkeypatch, busy_on=busy_on)
    if case == "no_sync_cap":
        fake.raw["caps"] = [c for c in fake.raw["caps"] if c != "sync"]
    if case == "idle_check_link":
        def dropped(requested, **kw):
            fake._rec("check_idle", requested)
            raise ConnectionResetError("fake link reset")
        monkeypatch.setattr(fake, "check_idle", dropped)
    if case in ("sync_link", "sync_unclear"):
        error = (ConnectionResetError("fake link reset")
                 if case == "sync_link" else RuntimeError("box said no"))

        def failing_sync(ra, dec):
            fake._rec("mount.sync", ra, dec)
            raise error
        monkeypatch.setattr(fake.mount, "sync", failing_sync)
    session, tel = await _asiair_tel(fake)
    try:
        expected = {
            "busy": (SyncRefused, "busy", ab.ASIAIR_BUSY_REASON),
            "sync_busy": (SyncRefused, "busy", ab.ASIAIR_BUSY_REASON),
            "no_sync_cap": (SyncRefused, "error", SYNC_NOT_SUPPORTED_REASON),
            "idle_check_link": (SyncUnverified, "",
                                SYNC_UNVERIFIED_LINK_BEFORE),
            "sync_link": (SyncUnverified, "", SYNC_UNVERIFIED_LINK_DURING),
            "sync_unclear": (SyncUnverified, "", SYNC_UNVERIFIED_UNCLEAR),
        }[case]
        with pytest.raises(expected[0]) as info:
            await tel.sync(5.9, 35.0)
        e = info.value
        assert type(e) is expected[0]
        assert (e.code, e.reason) == expected[1:]
        if expected[0] is SyncRefused:
            assert e.residual_deg == pytest.approx(2.5, abs=0.01)
        if case in ("no_sync_cap", "idle_check_link"):
            assert "mount.sync" not in fake.labels
        assert e.__context__ is None and e.__cause__ is None
        assert "box said no" not in str(e) and "reset" not in str(e)
    finally:
        await session.close()


# ------------------------------------------------------- through the real hub

async def test_a_refused_alpaca_sync_reaches_the_operator_whole(
        sim_hub, monkeypatch, bus_lines):
    """The real hub's ``solve_and_sync`` with a real Alpaca telescope whose
    mount ignores the sync: one warning, inside the UI's 137-character cut,
    with the action, and no coordinates.

    MUTANT N20 "the long draft reason" (``SYNC_NOT_MOVED_REASON`` = "the
    mount said the sync was done, but its position did not move; check the
    driver's sync settings", 97 characters): RED (observed), ``assert 144 <=
    137``."""
    fake = FakeAlpacaMount(equatorial_system=2, takes_sync=False)
    tel = alpaca_tel(fake)
    monkeypatch.setitem(sim_hub.devices, "telescope", tel)
    monkeypatch.setattr(sim_hub, "_mount_wants_jnow", None)
    _use_solver(monkeypatch, 10.0, 42.5)
    with pytest.raises(SyncRefused):
        await sim_hub.solve_and_sync(exposure_s=0.05)
    lines = [m for lvl, m, _ in bus_lines
             if lvl == "warning" and "refused the sync" in m]
    assert len(lines) == 1, bus_lines
    (msg,) = lines
    assert len(msg) <= 137, (len(msg), msg)
    assert "('OK')" in msg
    assert "check the driver's sync settings" in msg
    _assert_surfaced_ok(msg, (10.0, 42.5), (10.0, 40.0))


#: Every new reason with the code it is raised with ("" for unverified).
_REASONS = [
    (SYNC_NOT_MOVED_REASON, "OK"),
    (ab.ASIAIR_NOT_MOVED_REASON, "OK"),
    (SYNC_REFUSED_BY_DRIVER_REASON, "error"),
    (SYNC_PARKED_REASON, "error"),
    (SYNC_NOT_SUPPORTED_REASON, "error"),
    (NINA_SYNC_FAILED_REASON, "error"),
    (ab.ASIAIR_BUSY_REASON, "busy"),
    (SYNC_UNVERIFIED_READBACK, None),
    (SYNC_UNVERIFIED_LINK_DURING, None),
    (SYNC_UNVERIFIED_LINK_BEFORE, None),
    (SYNC_UNVERIFIED_UNCLEAR, None),
]

#: A 40-character driver-supplied name (Alpaca and NINA names are unbounded).
_LONG_NAME = "Telescope Simulator for .NET (ASCOM 7.1)"


@pytest.mark.parametrize("reason,code", _REASONS)
def test_every_new_sync_reason_fits_and_survives_the_humanizer(reason, code):
    """The hub's line (with the REAL ``_sync_reply_words``) fits the UI's
    137-character cut, the reason carries no digit, and the humanizer does
    not replace it. On the route's ``sync not taken: {e}`` line, built from
    the REAL message helpers with a 40-character name, the whole reason lies
    inside the first 137 characters.

    MUTANT N21 "NINA in the words" (``NINA_SYNC_FAILED_REASON`` = "NINA
    reported the sync as failed; its log says why"): RED (observed), the
    humanizer rewrites it beside 'error'.
    MUTANT N22 "the name first" (``refused_message`` returns
    ``f"{name}: sync refused: {reason}{where}"``): RED (observed) on
    SYNC_NOT_MOVED_REASON, the reason ends at character 157."""
    assert len(_LONG_NAME) == 40
    assert not any(ch.isdigit() for ch in reason), reason
    if code is None:
        hub_line = f"solved, but the mount did not confirm the sync: {reason}"
        exc_text = unverified_message(_LONG_NAME, reason, "no usable answer")
    else:
        hub_line = (f"solved, but the mount refused the sync "
                    f"({hub_module._sync_reply_words(code)}): {reason}")
        exc_text = refused_message(_LONG_NAME, reason, 2.5)
    assert len(hub_line) <= 137, (len(hub_line), hub_line)
    assert not _humanizer_rewrites(hub_line), hub_line
    route_line = f"sync not taken: {exc_text}"
    assert reason in route_line[:137], (route_line.index(reason) + len(reason),
                                        route_line)
    assert not _humanizer_rewrites(route_line), route_line


def test_the_shared_unverified_words_are_the_am5s_words():
    """One cause, one reason, across drivers (D-03)."""
    assert SYNC_UNVERIFIED_READBACK == zwo_am5.SYNC_UNVERIFIED_READBACK
    assert SYNC_UNVERIFIED_LINK_DURING == zwo_am5.SYNC_UNVERIFIED_LINK_DURING
    assert SYNC_UNVERIFIED_LINK_BEFORE == zwo_am5.SYNC_UNVERIFIED_LINK_BEFORE


# ------------------------------------------------------------------- timing

async def test_the_read_back_waits_between_reads(monkeypatch):
    """The report moves 0.15 s after the sync. With the 0.06 s sleep the
    sixth read comes at least 5 x (0.06 - 0.0156) = 0.222 s after t0, past
    0.15 plus one clock tick; without it six in-process reads finish within
    milliseconds.

    MUTANT N23 "no sleep between reads" (the ``await
    asyncio.sleep(SYNC_READBACK_RETRY_S)`` line deleted): RED (observed),
    SyncRefused."""
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRY_S", 0.06)
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRIES", 5)
    t0 = time.monotonic()

    async def read():
        if time.monotonic() - t0 >= 0.15:
            return 10.0, 42.5
        return 10.0, 40.0

    residual = await verify_sync("Fictional Mount", read, 10.0, 42.5)
    assert residual == pytest.approx(0.0, abs=1e-9)


async def test_a_read_back_that_hangs_is_unverified_within_the_bound(
        monkeypatch):
    """MUTANT N24 "wait_for removed" (``await read_position()`` bare in
    ``verify_sync``): RED (observed), the outer TimeoutError.
    MUTANT N25 "a timed-out read is retried" (``break`` -> ``continue`` in
    the timeout arm): RED (observed), ``assert 6 == 1``."""
    monkeypatch.setattr(sync_verify, "SYNC_READ_TIMEOUT_S", 0.05)
    fake = FakeAlpacaMount(takes_sync=True, hang_on={"rightascension"})
    tel = alpaca_tel(fake)
    with pytest.raises(SyncUnverified) as info:
        await asyncio.wait_for(tel.sync(10.0, 42.5), 5.0)
    e = info.value
    assert e.code == "OK"
    assert e.reason == SYNC_UNVERIFIED_READBACK
    assert "no answer in time" in str(e)
    assert fake.gets_after("rightascension", "synctocoordinates") == 1


async def test_a_slow_frame_transform_drops_the_alternate(monkeypatch):
    """The JNOW alternate is bounded; past the bound only the target counts.

    MUTANT N26 "the alternate is unbounded" (the ``wait_for`` in
    ``jnow_alternates`` removed): RED (observed), the outer TimeoutError."""
    def slow(ra, dec, when=None):
        time.sleep(1.0)
        return ra, dec + 0.5         # would have matched the read

    monkeypatch.setattr(hub_module, "precess_j2000_to_jnow", slow)
    monkeypatch.setattr(sync_verify, "SYNC_ALTERNATE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRIES", 0)

    async def read():
        return 10.0, 42.5

    with pytest.raises(SyncRefused) as info:
        await asyncio.wait_for(
            verify_sync("Fictional Mount", read, 10.0, 42.0,
                        alternates=jnow_alternates), 0.5)
    assert info.value.residual_deg == pytest.approx(0.5, abs=0.01)


async def test_the_asiair_read_back_uses_its_own_bound(monkeypatch):
    """The ASIAIR passes ``ASIAIR_SYNC_READ_TIMEOUT_S``, not the shared
    bound. Here the box's own bound is shrunk to 0.05 s and a post-sync read
    takes 1.0 s.

    MUTANT N27 "the ASIAIR uses the shared bound" (the
    ``read_timeout_s=ASIAIR_SYNC_READ_TIMEOUT_S`` argument dropped from the
    ``verify_sync`` call): RED (observed), the outer TimeoutError at 0.5 s."""
    _stub_precession(monkeypatch)
    monkeypatch.setattr(ab, "ASIAIR_SYNC_READ_TIMEOUT_S", 0.05)
    fake = _asiair_fake(monkeypatch)
    synced = {"done": False}

    def sync(ra, dec):
        fake._rec("mount.sync", ra, dec)
        fake.raw["RA"], fake.raw["Dec"] = float(ra), float(dec)
        synced["done"] = True

    real_info = fake.mount.info

    def info():
        if synced["done"]:
            time.sleep(1.0)
        return real_info()

    monkeypatch.setattr(fake.mount, "sync", sync)
    monkeypatch.setattr(fake.mount, "info", info)
    session, tel = await _asiair_tel(fake)
    try:
        with pytest.raises(SyncUnverified) as ei:
            await asyncio.wait_for(tel.sync(5.9, 35.0), 0.5)
        assert ei.value.reason == SYNC_UNVERIFIED_READBACK
    finally:
        synced["done"] = False
        await session.close()

# ------------------------------------------------------------ fix round 1

@pytest.mark.parametrize("driver", ["nina", "asiair"])
async def test_a_refusal_residual_counts_the_jnow_frame(monkeypatch, driver):
    """A refused sync's one residual read is weighed against the target AND
    its JNOW, as the read-back is: the mount's report sits exactly at
    JNOW(T), more than a degree from T, so the residual is about 0. The
    resume ladder weighs ``residual_deg`` against its 5 deg rule.

    MUTANT G7 "the NINA refusal drops the alternate" (``alternates=
    jnow_alternates`` removed from ``NinaTelescope.sync``'s refused arm):
    RED (observed) on nina.
    MUTANT G6 "the ASIAIR refusal drops the alternate" (the same argument
    removed from ``AsiairTelescope._sync_refused``): RED (observed) on
    asiair."""
    _stub_precession(monkeypatch)
    if driver == "nina":
        # The transport NINA reports (10.0, 40.0) = JNOW(9.75, 39.0).
        tel, client = _transport_nina(_answers("success_false"))
        target = (9.75, 39.0)
        try:
            with pytest.raises(SyncRefused) as info:
                await tel.sync(*target)
        finally:
            await client.aclose()
    else:
        # The fake box reports (5.9, 32.5) = JNOW(5.65, 31.5).
        fake = _asiair_fake(monkeypatch, busy_on={"check_idle"})
        target = (5.65, 31.5)
        session, tel = await _asiair_tel(fake)
        try:
            with pytest.raises(SyncRefused) as info:
                await tel.sync(*target)
        finally:
            await session.close()
    assert info.value.code in ("error", "busy")
    assert info.value.residual_deg == pytest.approx(0.0, abs=0.01)


async def test_the_asiair_refusal_read_uses_its_own_bound(monkeypatch):
    """The busy refusal's residual read passes ``ASIAIR_SYNC_READ_TIMEOUT_S``
    too. The box's bound is shrunk to 0.05 s and every mount info read after
    the idle check takes 1.0 s: the refusal comes back inside 0.5 s with no
    residual.

    MUTANT G10 "the ASIAIR refusal read uses the shared bound" (the
    ``read_timeout_s=ASIAIR_SYNC_READ_TIMEOUT_S`` argument dropped from
    ``_sync_refused``): RED (observed), the outer TimeoutError at 0.5 s."""
    _stub_precession(monkeypatch)
    monkeypatch.setattr(ab, "ASIAIR_SYNC_READ_TIMEOUT_S", 0.05)
    fake = _asiair_fake(monkeypatch, busy_on={"check_idle"})
    armed = {"on": False}
    real_check = fake.check_idle
    real_info = fake.mount.info

    def check_idle(requested, **kw):
        armed["on"] = True
        return real_check(requested, **kw)

    def info():
        if armed["on"]:
            time.sleep(1.0)
        return real_info()

    monkeypatch.setattr(fake, "check_idle", check_idle)
    monkeypatch.setattr(fake.mount, "info", info)
    session, tel = await _asiair_tel(fake)
    try:
        with pytest.raises(SyncRefused) as ei:
            await asyncio.wait_for(tel.sync(5.9, 35.0), 0.5)
        assert ei.value.reason == ab.ASIAIR_BUSY_REASON
        assert ei.value.residual_deg is None
    finally:
        armed["on"] = False
        # The abandoned read keeps the link until its thread returns.
        async with tel._link._lock:
            pass
        await session.close()


async def test_an_asiair_call_cancelled_mid_rpc_keeps_the_link():
    """A cancel cannot stop a thread inside libasi's transport, so the link
    lock stays held until that thread returns: the next RPC waits for it
    rather than running beside it, where either could take the other's
    reply. The cancel itself propagates at once.

    MUTANT G-link "the lock is released at the cancel" (``_Link.call`` back
    to ``async with self._lock: value = await asyncio.to_thread(...)``):
    RED (observed), the second call finishes while the first thread is
    still inside."""
    import threading
    link = ab._Link(client=None, host="asiair.invalid")
    inside = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def slow():
        order.append("slow in")
        inside.set()
        release.wait(5.0)
        order.append("slow out")
        return "late"

    def quick():
        order.append("quick")
        return "ok"

    first = asyncio.create_task(link.call(slow, what="slow read"))
    assert await asyncio.to_thread(inside.wait, 5.0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(link.call(quick, what="quick read"))
    try:
        done, _ = await asyncio.wait({second}, timeout=0.5)
        assert not done, order
        assert order == ["slow in"], order
    finally:
        release.set()
    assert await asyncio.wait_for(second, 5.0) == "ok"
    assert order == ["slow in", "slow out", "quick"], order
    assert not link._lock.locked()


async def test_the_last_read_decides_after_an_earlier_failure(monkeypatch):
    """A read that fails, then reads that answer and still miss: the LAST
    read answered, so this is a refusal with its residual, not unverified.

    MUTANT G-last "a failure is remembered" (``last_failed = None`` deleted
    from ``verify_sync``): RED (observed), SyncUnverified."""
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRIES", 2)
    calls = {"n": 0}

    async def read():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("fake read failure")
        return 10.0, 40.0

    with pytest.raises(SyncRefused) as info:
        await verify_sync("Fictional Mount", read, 10.0, 42.5)
    assert info.value.residual_deg == pytest.approx(2.5, abs=0.01)
    assert calls["n"] == 3


@pytest.mark.parametrize("answer", [None, (1.0,)], ids=["none", "one_value"])
async def test_an_unparseable_read_stays_inside_the_contract(monkeypatch,
                                                             answer):
    """A read that returns something that is not a position (None, a
    1-tuple) is unverified in fixed words; no TypeError or IndexError leaks
    past ``verify_sync`` into the hub's solve-failed arm.

    MUTANT G13 "only ValueError is unreadable" (``_parse``'s ``except
    (TypeError, ValueError, IndexError)`` narrowed to ``except
    ValueError``): RED (observed), TypeError on none, IndexError on
    one_value."""
    monkeypatch.setattr(sync_verify, "SYNC_READBACK_RETRIES", 1)

    async def read():
        return answer

    with pytest.raises(SyncUnverified) as info:
        await verify_sync("Fictional Mount", read, 10.0, 42.5)
    assert info.value.reason == SYNC_UNVERIFIED_READBACK
    assert str(info.value).endswith("; the last read got an unreadable answer)")


class _ReadsDieAfterSync(FakeAlpacaMount):
    """Answers every read until the sync, then answers every read with HTTP
    500, so the read-back cannot confirm the sync."""

    def _put(self, method: str, params: dict) -> httpx.Response:
        if method == "synctocoordinates":
            self.read_http_status = 500
        return super()._put(method, params)


async def test_an_unverified_alpaca_sync_reaches_the_operator_whole(
        sim_hub, monkeypatch, bus_lines):
    """The real hub's ``solve_and_sync`` line for the LONGEST unverified
    reason (``SYNC_UNVERIFIED_READBACK``), with the hub's real prefix: one
    warning, inside the UI's 137-character cut, not rewritten by the
    humanizer, and no coordinates.

    MUTANT N30 "a longer unverified prefix" (``"solved, but the mount did
    not confirm the sync: "`` in ``hub.solve_and_sync`` given ten more
    characters): RED (observed), the line runs past 137."""
    fake = _ReadsDieAfterSync(equatorial_system=2)
    tel = alpaca_tel(fake)
    monkeypatch.setitem(sim_hub.devices, "telescope", tel)
    monkeypatch.setattr(sim_hub, "_mount_wants_jnow", None)
    _use_solver(monkeypatch, 10.0, 42.5)
    with pytest.raises(SyncUnverified) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05)
    assert info.value.reason == SYNC_UNVERIFIED_READBACK
    lines = [m for lvl, m, _ in bus_lines
             if lvl == "warning" and "did not confirm the sync" in m]
    assert len(lines) == 1, bus_lines
    (msg,) = lines
    assert msg.endswith(SYNC_UNVERIFIED_READBACK), msg
    assert len(msg) <= 137, (len(msg), msg)
    assert not _humanizer_rewrites(msg), msg
    _assert_surfaced_ok(msg, (10.0, 42.5), (10.0, 40.0))
