# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#926 and #927: two more driver error texts that quoted a reply.

The same class as #863, #906 and #907 (see ``test_906_907_driver_error_texts``
for why): a figure a driver prints is a site oracle once the mount sits at
home, and a key-name filter cannot withhold it. Every figure below is made up.

#926. ``AlpacaCamera._download_image`` raised ``DeviceError(ErrorMessage)``
when the JSON ``imagearray`` reply carried a non-zero ErrorNumber. That is the
driver's own words, in an exception that reaches the log lines and the capture
failure message. It now goes through ``AlpacaConnection._unwrap`` like every
other Alpaca reply.

#927. ``_Link.call`` and ``_Link.connect`` wrapped ``str(exc)`` of whatever
libasi raised into the ``DeviceError`` and into ``last_error``. libasi's
``ASIAIRError`` is built from the ``error`` string of the box's reply
(``asiair/transport.py``: ``raise ASIAIRError(result["error"], ...)``, message
``[code] method: error``), and Python's own errors out of its reply parsing
can quote a token of it, so no libasi text is known to be free of a reply. A
failure is now named by its class and the call. The original exception stays
on ``__cause__``: ``AsiairTelescope.sync`` reads its class to tell a refusal
from a dead link.
"""
from __future__ import annotations

import struct

import httpx
import numpy as np
import pytest

import astrodeck.devices.backends.asiair_backend as ab
from astrodeck.devices.alpaca import (AlpacaCamera, AlpacaConnection,
                                      AlpacaReplyError)
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sync_verify import (SYNC_UNVERIFIED_LINK_DURING,
                                           SYNC_UNVERIFIED_UNCLEAR,
                                           SyncUnverified)

from test_906_907_driver_error_texts import (BODY, DEC_DMS, RA_HMS, TELLS,
                                             _alpaca_json,
                                             _assert_no_position,
                                             _everything_said)
from test_asiair_backend import _conn, _open, fake  # noqa: F401 (fixture)


# ------------------------------------------------------------------- Alpaca

def _camera(handler) -> tuple[AlpacaCamera, AlpacaConnection]:
    conn = AlpacaConnection("alpaca.invalid", 11111)
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cam = AlpacaCamera(conn, 0, "Fictional Alpaca Camera")
    cam.sensor_width = 8
    cam.sensor_height = 6
    cam.max_gain = 0
    return cam, conn


@pytest.mark.parametrize("number, shown", [
    (0x400, "0x400"),
    (0x500, "0x500"),
    (-2147220478, "-2147220478"),
])
async def test_alpaca_an_image_download_error_message_is_never_quoted(
        number, shown):
    """The ErrorNumber is kept (the one part that can be looked up); the
    driver's ErrorMessage is not.

    MUTANT C1 "the ErrorMessage passed through" (``raise
    DeviceError(body.get("ErrorMessage", ...))`` back in ``_download_image``):
    RED on the leak assertion."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=_alpaca_json(None, number, BODY))

    cam, conn = _camera(handler)
    try:
        with pytest.raises(AlpacaReplyError) as exc:
            await cam._download_image()
        assert seen == ["/api/v1/camera/0/imagearray"], seen
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert f"error {shown}" in said, said
        assert "camera/0/imagearray" in said, said
        assert exc.value.http_status == 200
        assert exc.value.error_number == number
    finally:
        await conn.http.aclose()


async def test_alpaca_an_image_download_http_error_names_status_route_and_size():
    """An HTTP error on the image route used to reach ``r.json()`` and escape
    as a ``JSONDecodeError`` that is not a ``DeviceError`` at all. It is now an
    ordinary stated-reason failure, and the body is not quoted.

    MUTANT C1 as above: RED, the text is not a DeviceError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=BODY)

    cam, conn = _camera(handler)
    try:
        with pytest.raises(DeviceError) as exc:
            await cam._download_image()
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "HTTP 500" in said, said
        assert "camera/0/imagearray" in said, said
        assert f"{len(BODY)} bytes" in said, said
    finally:
        await conn.http.aclose()


async def test_alpaca_an_exposure_whose_download_fails_says_no_position():
    """The issue's own case, end to end: the capture reaches the download,
    the driver answers it with an error that names a position, and what the
    capture raises (which becomes the failure message) names none."""
    def handler(request: httpx.Request) -> httpx.Response:
        route = request.url.path.rsplit("/", 1)[-1]
        if route == "imageready":
            return httpx.Response(200, json=_alpaca_json(True))
        if route == "imagearray":
            return httpx.Response(200, json=_alpaca_json(None, 0x500, BODY))
        return httpx.Response(200, json=_alpaca_json(None))

    cam, conn = _camera(handler)
    try:
        with pytest.raises(DeviceError) as exc:
            await cam.expose(0.01, 100, 30, binning=1)
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "camera/0/imagearray" in said, said
        assert cam._exposing is False
    finally:
        await conn.http.aclose()


async def test_alpaca_a_binary_image_error_still_says_no_position():
    """The ImageBytes arm already names only the number: the message the spec
    puts after the header is never read. Pinned so it stays so; this passes
    on the code before and after #926."""
    header = struct.pack("<11i", 1, 0x500, 11, 22, 44, 0, 8, 2, 0, 0, 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=header + BODY.encode(),
            headers={"content-type": "application/imagebytes"})

    cam, conn = _camera(handler)
    try:
        with pytest.raises(DeviceError) as exc:
            await cam._download_image()
        said = _everything_said(exc.value)
        _assert_no_position(said)
        assert "ImageBytes error" in said, said
    finally:
        await conn.http.aclose()


async def test_alpaca_a_good_json_image_is_decoded_unchanged():
    """The guard against over-reach: a 200 with ErrorNumber 0 still returns
    its Value, as the transposed uint16 array."""
    value = [[1, 2, 3], [4, 5, 70000]]          # [x][y]: 2 wide, 3 high

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_alpaca_json(value))

    cam, conn = _camera(handler)
    try:
        img = await cam._download_image()
        assert img.dtype == np.uint16
        assert img.tolist() == [[1, 4], [2, 5], [3, 65535]]
    finally:
        await conn.http.aclose()


# ------------------------------------------------------------------- ASIAIR

class ASIAIRError(Exception):
    """Stands in for ``asiair.transport.ASIAIRError``: the same class name,
    the same ``code`` and ``method`` attributes and the same message shape,
    the box's own ``error`` string inside ``[code] method: error``. That the
    message is the box's words is the whole point of the double."""

    def __init__(self, message, code, method):
        self.code = code
        self.method = method
        super().__init__(f"[{code}] {method}: {message}")


#: What libasi can raise out of a call, each carrying BODY (or a token of it)
#: in its text, and the name a log line is allowed to show for it.
FAILURES = [
    pytest.param(lambda: ASIAIRError(BODY, 253, "scope_get_info"),
                 "ASIAIRError code 253", id="box-error-with-code"),
    pytest.param(lambda: ASIAIRError(BODY, -1, "scope_sync"),
                 "ASIAIRError code -1", id="box-error-negative-code"),
    pytest.param(lambda: RuntimeError(BODY), "RuntimeError", id="runtime"),
    # A Python error out of libasi's reply parsing quotes the token it choked
    # on, so no class of libasi exception can be allow-listed to keep its text.
    pytest.param(
        lambda: ValueError(f"could not convert string to float: {RA_HMS!r}"),
        "ValueError", id="parse-error-quotes-a-token"),
    pytest.param(lambda: OSError(BODY), "OSError", id="os-error"),
]


@pytest.mark.parametrize("make, named", FAILURES)
async def test_asiair_a_failed_call_names_the_class_and_the_call(
        fake, make, named):
    """The ``DeviceError`` and ``last_error`` name the exception class (and the
    box's numeric code), the call and the host; libasi's text is in neither.
    The original stays on ``__cause__`` for ``sync``'s classification.

    MUTANT L1 "the text back in the message" (``{exc}`` in place of ``{shown}``
    in the ``DeviceError`` of ``_Link.call``): RED on the leak assertion.
    MUTANT L2 "the text back in last_error" (``{exc}`` in place of ``{shown}``
    in ``last_error`` of ``_Link.call``): RED on the last_error assertion."""
    injected = make()

    session = await _open()
    tel = await session.get_device("telescope", _conn())

    def info():
        raise injected

    fake.mount.info = info      # after connect, which reads the mount too
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        text = str(exc.value)
        _assert_no_position(text)
        assert text.startswith("ASIAIR asiair.invalid: read mount info failed"), text
        assert named in text, text
        last = session._link.last_error
        assert last is not None
        _assert_no_position(last)
        assert last == f"read mount info: {named}", last
        # The class of the original is what sync() classifies on.
        assert exc.value.__cause__ is injected
    finally:
        await session.close()


@pytest.mark.parametrize("code", [RA_HMS, DEC_DMS, True, None, 7.5])
async def test_asiair_a_code_that_is_not_an_integer_is_not_shown(fake, code):
    """The box's ``code`` is shown only when it is an integer. A box that
    answers with anything else in that field (a string, a float, a bool) gets
    its class named and nothing more.

    MUTANT L3 "any code shown" (the ``isinstance(code, int)`` test dropped from
    ``_shown_failure``): RED for the string cases."""
    session = await _open()
    tel = await session.get_device("telescope", _conn())

    def info():
        raise ASIAIRError(BODY, code, "scope_get_info")

    fake.mount.info = info      # after connect, which reads the mount too
    try:
        with pytest.raises(DeviceError) as exc:
            await tel.get_position()
        _assert_no_position(str(exc.value))
        assert " ASIAIRError (" in str(exc.value), str(exc.value)
        assert "code" not in session._link.last_error, session._link.last_error
        for tell in TELLS:
            assert tell not in session._link.last_error
    finally:
        await session.close()


@pytest.mark.parametrize("make, named", FAILURES)
async def test_asiair_a_failed_connect_names_the_class_not_the_text(
        fake, make, named):
    """``connect`` ran ``test_connection`` on both ports, so the box's own
    ``error`` can come back through it as well.

    MUTANT L4 "the text back in connect" (``{exc}`` in place of ``{shown}`` in
    the ``DeviceError`` or in ``last_error`` of ``_Link.connect``): RED."""
    injected = make()

    def connect(heartbeat=True):
        raise injected

    fake.connect = connect
    link = ab._Link(fake, "asiair.invalid")
    with pytest.raises(DeviceError) as exc:
        await link.connect()
    text = str(exc.value)
    _assert_no_position(text)
    assert text.startswith("ASIAIR asiair.invalid: could not connect"), text
    assert named in text, text
    assert link.connected is False
    assert link.last_error == f"connect: {named}", link.last_error
    _assert_no_position(link.last_error)
    assert exc.value.__cause__ is injected


async def test_asiair_a_failed_open_through_the_backend_says_no_position(fake):
    """The same through the way a profile really connects."""
    def connect(heartbeat=True):
        raise ASIAIRError(BODY, 253, "test_connection")

    fake.connect = connect
    with pytest.raises(DeviceError) as exc:
        await _open()
    _assert_no_position(str(exc.value))
    assert "ASIAIRError code 253" in str(exc.value), str(exc.value)


async def test_asiair_health_and_the_probe_say_no_position(fake):
    """The status surfaces read ``last_error`` and the probe reads the text of
    the connect failure; both are fed by the same two sites."""
    session = await _open()

    def activity():
        raise ASIAIRError(BODY, 253, "get_app_state")

    fake.get_activity = activity
    health = await session.health()
    assert health["ok"] is False
    _assert_no_position(health["last_error"])
    assert "ASIAIRError code 253" in health["last_error"], health["last_error"]
    assert "read app state" in health["last_error"], health["last_error"]
    await session.close()

    def connect(heartbeat=True):
        raise ASIAIRError(BODY, 253, "test_connection")

    fake.connect = connect
    probe = await ab.probe_asiair("asiair.invalid")
    assert probe["reachable"] is False
    _assert_no_position(probe["error"])
    assert "ASIAIRError code 253" in probe["error"], probe["error"]


async def test_asiair_a_failed_sync_is_still_classified_by_the_cause(fake):
    """The text is gone from the message, but ``sync`` still tells a dead link
    from an answer by the class on ``__cause__``: an ``OSError`` is an
    unverified link failure, a box error is an unclear one, and neither
    message says where the mount was."""
    session = await _open()
    tel = await session.get_device("telescope", _conn())
    try:
        for injected, reason in (
                (OSError(BODY), SYNC_UNVERIFIED_LINK_DURING),
                (ASIAIRError(BODY, 253, "scope_sync"), SYNC_UNVERIFIED_UNCLEAR)):
            def sync(ra, dec, _e=injected):
                raise _e

            fake.mount.sync = sync
            with pytest.raises(SyncUnverified) as exc:
                await tel.sync(5.9, 32.5)
            assert exc.value.reason == reason, exc.value.reason
            _assert_no_position(str(exc.value))
    finally:
        await session.close()
