# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#906 and #907: a driver's error text never names a mount position.

At the home position a mount points at the pole, so any figure it reports is
a site oracle: Dec is the latitude, and RA follows local sidereal time, which
with the log timestamp gives the longitude (#140, #166). A key-name filter
cannot withhold a value that a driver computes and prints itself, so the error
texts say the status, the route and the SIZE of what came back, never its
words (the #863 shape, which the AM5 driver already follows).

#906. The Alpaca and NINA clients put the first 120 to 200 characters of an
HTTP reply into the exception, and the driver's own ErrorMessage (Alpaca) or
Error (NINA) as well. An ASCOM mount driver's error for a position read, a
slew, or the site write names the numbers it was asked about, and NINA's mount
info route carries the position and the site itself. ``NinaClient.last_error``
is ``str(e)[:200]`` of the same text, and the status surfaces show it.

#907. The ASIAIR settle timeout printed the last-read RA and Dec and the
commanded target. A slew to the zenith is a target computed from the site.

These cases grade the text a person reading the logs would see, through the
REAL ``AlpacaConnection`` and ``NinaClient`` behind ``httpx.MockTransport``
and the real ASIAIR telescope over the repo's fake libasi client, with every
chained exception included. Every figure below is made up.
"""
from __future__ import annotations

import json

import httpx
import pytest

import astrodeck.devices.backends.asiair_backend as ab
from astrodeck.devices.alpaca import (AlpacaConnection, AlpacaReplyError,
                                      AlpacaTelescope)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.nina import (NinaClient, NinaGuider, NinaReplyError,
                                    NinaTelescope)

from test_asiair_backend import _conn, _open, fake  # noqa: F401 (fixture)

#: Made-up position, spelled the ways a driver writes one. None of these may
#: reach an error text, a ``last_error`` or an exception chained behind them.
RA_HMS = "07:23:41"
DEC_DMS = "+41:16:09"
RA_DEG = "7.3947"
DEC_DEG = "41.2692"
TELLS = (RA_HMS, DEC_DMS, RA_DEG, DEC_DEG, "07:23", "41:16")

#: What a driver or a proxy might say about a position.
BODY = (f"driver fault: telescope at RA {RA_HMS} Dec {DEC_DMS} "
        f"({RA_DEG}h, {DEC_DEG}deg) is not allowed")


def _everything_said(exc: BaseException) -> str:
    """The exception's text plus every exception a traceback would print
    behind it (``__cause__``, else ``__context__`` unless suppressed)."""
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        if cur.__cause__ is not None:
            cur = cur.__cause__
        elif cur.__suppress_context__:
            cur = None
        else:
            cur = cur.__context__
    return "\n".join(parts)


def _assert_no_position(said: str) -> None:
    for tell in TELLS:
        assert tell not in said, f"{tell!r} in {said!r}"


def _alpaca_json(value=None, number=0, message=""):
    return {"Value": value, "ErrorNumber": number, "ErrorMessage": message,
            "ClientTransactionID": 0, "ServerTransactionID": 0}


def _alpaca_conn(handler) -> AlpacaConnection:
    conn = AlpacaConnection("alpaca.invalid", 11111)
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return conn


# ------------------------------------------------------------------- Alpaca

@pytest.mark.parametrize("verb, dev_type, method, status", [
    # The position reads, a slew and the site write: the routes whose
    # replies a mount driver fills with the numbers it was asked about.
    ("get", "telescope", "rightascension", 500),
    ("get", "telescope", "declination", 400),
    ("put", "telescope", "slewtocoordinatesasync", 400),
    ("put", "telescope", "sitelatitude", 400),
    # Not a mount, but a roof or a dome driver that checks the mount's park
    # position quotes it: the transport does not look at who is speaking.
    ("get", "dome", "shutterstatus", 500),
])
async def test_alpaca_an_http_error_names_status_route_and_size(
        verb, dev_type, method, status):
    """MUTANT A1 "the body quoted" (``r.text[:200]`` back in the HTTP-error
    text of ``AlpacaConnection._unwrap``): RED on the leak assertion."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=BODY)

    conn = _alpaca_conn(handler)
    try:
        with pytest.raises(AlpacaReplyError) as exc:
            if verb == "get":
                await conn.get(dev_type, 0, method)
            else:
                await conn.put(dev_type, 0, method, Value=1.0)
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert f"HTTP {status}" in said, said
        assert f"{dev_type}/0/{method}" in said, said
        assert f"{len(BODY)} bytes" in said, said
        # What the sync path reads off the exception is unchanged.
        assert exc.value.http_status == status
        assert exc.value.error_number is None
    finally:
        await conn.http.aclose()


@pytest.mark.parametrize("number, shown", [
    (0x400, "0x400"),
    (0x500, "0x500"),
    (-2147220478, "-2147220478"),
])
async def test_alpaca_a_driver_error_message_is_never_quoted(number, shown):
    """The ErrorNumber is kept (the one part that can be looked up); the
    ErrorMessage is the driver's own words and is not.

    MUTANT A2 "the ErrorMessage passed through" (``body.get("ErrorMessage",
    ...)`` back as the text of the ErrorNumber arm): RED on the leak
    assertion."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_alpaca_json(None, number, BODY))

    conn = _alpaca_conn(handler)
    try:
        with pytest.raises(AlpacaReplyError) as exc:
            await conn.get("telescope", 0, "rightascension")
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert f"error {shown}" in said, said
        assert "telescope/0/rightascension" in said, said
        assert exc.value.http_status == 200
        assert exc.value.error_number == number
    finally:
        await conn.http.aclose()


async def test_alpaca_an_unreadable_error_number_still_says_nothing_of_the_message():
    """``ErrorNumber`` that is not a number is an error whose number cannot
    be read (``error_number`` None); there is no sentinel to print."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_alpaca_json(None, "abc", BODY))

    conn = _alpaca_conn(handler)
    try:
        with pytest.raises(AlpacaReplyError) as exc:
            await conn.get("telescope", 0, "declination")
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert exc.value.http_status == 200
        assert exc.value.error_number is None
        assert "0x" not in said and "-1" not in said, said
    finally:
        await conn.http.aclose()


async def test_alpaca_a_failed_position_read_through_the_driver_says_no_position():
    """The issue's own case, end to end: ``get_position`` on a telescope whose
    driver answers the read with an error that names the position."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=BODY)

    conn = _alpaca_conn(handler)
    tel = AlpacaTelescope(conn, 0, "Fictional Alpaca Mount")
    tel.connected = True
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "rightascension" in said, said
    finally:
        await conn.http.aclose()


async def test_alpaca_a_good_reply_is_unwrapped_unchanged():
    """The guard against over-reach: a 200 with ErrorNumber 0 still returns
    its Value."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_alpaca_json(12.5))

    conn = _alpaca_conn(handler)
    try:
        assert await conn.get("telescope", 0, "rightascension") == 12.5
    finally:
        await conn.http.aclose()


# --------------------------------------------------------------------- NINA

def _nina(handler) -> tuple[NinaClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NinaClient("nina.invalid", 1888, http=http), http


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_nina_an_http_error_names_status_path_and_size(status):
    """MUTANT N1 "the body quoted" (``r.text[:160]`` back in the HTTP-error
    text of ``NinaClient.get``): RED on the leak assertion. ``last_error`` is
    ``str(e)[:200]`` of the same text, so it is checked too."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=BODY)

    client, http = _nina(handler)
    try:
        with pytest.raises(NinaReplyError) as exc:
            await client.get("/equipment/mount/info")
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert f"HTTP {status}" in said, said
        assert "/equipment/mount/info" in said, said
        assert f"{len(BODY)} bytes" in said, said
        assert exc.value.http_status == status
        assert client.last_error, "last_error must still be stamped"
        _assert_no_position(client.last_error)
        assert f"HTTP {status}" in client.last_error
    finally:
        await http.aclose()


async def test_nina_success_false_does_not_quote_nina_s_error():
    """HTTP 200 with ``Success: false``: NINA's ``Error`` is its own words,
    as free as a driver's ErrorMessage.

    MUTANT N2 "the Error passed through" (``pick(body, "Error", ...)`` back
    as the text of the ``Success: false`` arm): RED on the leak assertion."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Success": False, "Error": BODY,
                                         "Response": None})

    client, http = _nina(handler)
    try:
        with pytest.raises(NinaReplyError) as exc:
            await client.get("/equipment/mount/slew", ra=110.9, dec=41.2)
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "/equipment/mount/slew" in said, said
        assert exc.value.http_status == 200
        _assert_no_position(client.last_error or "")
        assert client.last_error and "/equipment/mount/slew" in client.last_error
    finally:
        await http.aclose()


async def test_nina_a_binary_endpoint_error_names_status_path_and_size():
    """MUTANT N3 "the image body quoted" (``r.text[:120]`` back in
    ``get_bytes``): RED on the leak assertion."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=BODY)

    client, http = _nina(handler)
    try:
        with pytest.raises(DeviceError) as exc:
            await client.get_bytes("/image/0")
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "HTTP 500" in said and "/image/0" in said, said
        assert f"{len(BODY)} bytes" in said, said
        _assert_no_position(client.last_error or "")
        assert client.last_error and "HTTP 500" in client.last_error
    finally:
        await http.aclose()


async def test_nina_a_failed_position_read_through_the_driver_says_no_position():
    """``NinaTelescope.get_position`` reads ``/equipment/mount/info``, which
    carries the position and the site. An error from it names neither, in the
    exception or in the status surfaces' ``last_error``."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=json.dumps(
            {"Success": False, "Error": BODY, "StatusCode": 500}))

    client, http = _nina(handler)
    tel = NinaTelescope(client, "Fictional Bridge Mount")
    tel.connected = True
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        _assert_no_position(_everything_said(exc.value))
        _assert_no_position(client.last_error or "")
    finally:
        await http.aclose()


async def test_nina_a_refused_guider_start_still_says_what_to_check():
    """The guide-start failure line used to carry NINA's ``Error`` (or, for
    an empty one, a hint). NINA's words are no longer quoted, so the hint
    is what an operator gets, and it still says where to look."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Success": False, "Error": BODY})

    client, http = _nina(handler)
    guider = NinaGuider(client)
    try:
        with pytest.raises(DeviceError) as exc:
            await guider.start_guiding()
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert said.startswith("DeviceError: NINA guider start failed: "), said
        assert "PHD2" in said, said
    finally:
        await http.aclose()


async def test_nina_a_good_reply_is_unwrapped_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Success": True,
                                         "Response": {"Connected": True}})

    client, http = _nina(handler)
    try:
        assert await client.get("/equipment/mount/info") == {"Connected": True}
        assert client.last_error is None
    finally:
        await http.aclose()


# ------------------------------------------------------------------- ASIAIR

#: Where the fake mount sits (``fake.raw`` RA 5.9 Dec 32.5 unless a test says
#: otherwise) and where the slew was sent. The second pair is the zenith of a
#: made-up site, which is exactly the target that must not be printed.
SITTING = (5.9, 32.5)
TARGET = (12.3456, -20.7654)


def _position_spellings(*pairs: tuple[float, float]) -> list[str]:
    out: list[str] = []
    for ra, dec in pairs:
        out += [f"{ra}", f"{dec}", f"{ra:.1f}", f"{dec:.1f}", f"{ra:.4f}",
                f"{dec:+.4f}", f"{dec:.4f}", f"{ra:.2f}", f"{dec:.2f}"]
    return out


async def test_asiair_a_settle_timeout_reports_separation_never_a_position(
        fake, monkeypatch):
    """The timeout says that the slew did not settle, that the mount was
    stopped, and how far it is from the target. It names neither where the
    mount was last read nor where it was sent.

    MUTANT S1 "the last-read position back" (``Last read RA ... Dec ...``
    restored in ``_wait_stopped``): RED on the position assertions.
    MUTANT S2 "the target back" (``requested RA ... Dec ...`` restored): RED
    on the target assertions."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    monkeypatch.setattr(ab, "SLEW_TIMEOUT_S", 0.05)
    fake.raw["RA"], fake.raw["Dec"] = SITTING
    fake.raw["move_status"] = "none"      # the box insists it is stopped
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.slew(*TARGET)
        said = _everything_said(exc.value)
        assert "did not settle within" in said, said
        assert "stopped the mount" in said, said
        sep = f"{ab._sky_delta_deg(SITTING, TARGET):.2f}"
        rest = said.replace(sep, "")        # a separation may share digits
        for spelling in _position_spellings(SITTING, TARGET):
            assert spelling not in rest, f"{spelling!r} in {said!r}"
        assert f"{sep} deg from the target" in said, said
        assert "mount.stop" in fake.labels
    finally:
        await session.close()


async def test_asiair_a_settle_timeout_with_no_target_says_only_the_timeout(
        fake, monkeypatch):
    """Without a commanded destination there is no separation to report, so
    the text is the timeout and the stop and nothing else."""
    monkeypatch.setattr(ab, "POLL_S", 0.001)
    fake.raw["RA"], fake.raw["Dec"] = SITTING
    fake.raw["move_status"] = "goto"      # never clears
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    try:
        with pytest.raises(DeviceError) as exc:
            await tel._wait_stopped(0.05, "slew")
        said = _everything_said(exc.value)
        assert "did not settle within" in said and "stopped the mount" in said
        for spelling in _position_spellings(SITTING):
            assert spelling not in said, f"{spelling!r} in {said!r}"
        assert "deg from the target" not in said, said
        assert "mount.stop" in fake.labels
    finally:
        await session.close()
