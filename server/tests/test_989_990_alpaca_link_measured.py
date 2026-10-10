# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#989 and #990: an Alpaca device's ``connected`` is MEASURED, on every device
type and on every read the camera makes.

#16 (WP-47) made the CAMERA's ``connected`` a measurement: a transport failure
(no HTTP response at all) sets it false, so the reconnect gate
(``sequence/engine.py._reconnect_gate``) sees a server that vanished. #966 did
the same for the answer NotConnected (0x407), on the base class. The transport
half stayed on ``AlpacaCamera._get``/``_put``, so a telescope, focuser, filter
wheel or rotator whose server vanished kept reading 'connected' for good (#989).

Two camera reads still went round it. The image download is a direct GET, and
its ImageBytes arm raised a plain ``DeviceError`` for a non-zero header error
number, so an ImageBytes 0x407 never reached ``_note_not_connected`` (#990).

Real ``AlpacaConnection`` objects over ``httpx.MockTransport`` (and, once, over a
real socket that goes away), the real device classes, no hardware. Each test
names the mutant of ``devices/alpaca.py`` it was shown red under (applied to a
byte copy and restored from that copy, md5-checked).
"""
from __future__ import annotations

import asyncio
import json
import struct

import httpx
import pytest

from astrodeck.devices.alpaca import (
    DEVICE_CLASSES,
    AlpacaCamera,
    AlpacaConnection,
    AlpacaReplyError,
    AlpacaTelescope,
)
from astrodeck.devices.base import DeviceError

NOT_CONNECTED = 0x407
#: A figure and a phrase a driver might print in its own words. Neither may
#: reach an exception text (#906); they ride in the ImageBytes body below.
DRIVER_WORDS = "camera #0 says RA 12h34m56s is not connected"


def _json(value=None, number=0, message=""):
    return {"Value": value, "ErrorNumber": number, "ErrorMessage": message,
            "ClientTransactionID": 0, "ServerTransactionID": 0}


def _imagebytes(error_number: int) -> httpx.Response:
    """An ImageBytes reply whose header carries ``error_number``. Past the
    44-byte header the spec puts the driver's message (a UTF-8 string)."""
    header = struct.pack("<11i", 1, error_number, 11, 22, 44, 0, 8, 2, 0, 0, 0)
    return httpx.Response(200, content=header + DRIVER_WORDS.encode(),
                          headers={"content-type": "application/imagebytes"})


async def _device(dev_type: str, handler):
    """A real device of ``dev_type`` over a real connection whose HTTP client
    answers from ``handler``; already believing it is connected."""
    conn = AlpacaConnection("alpaca.invalid", 11111)
    await conn.http.aclose()
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dev = DEVICE_CLASSES[dev_type](conn, 0, "Fictional Alpaca Device")
    dev.connected = True
    return dev


def _camera_that_has_read_its_sensor(dev: AlpacaCamera) -> AlpacaCamera:
    dev.sensor_width = 8
    dev.sensor_height = 6
    dev.max_gain = 0
    return dev


def _raises(error_type):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("no answer", request=request)
    return handler


# --------------------------------------------------------------------------
# #989: the transport half of the measurement, on every device type
# --------------------------------------------------------------------------
@pytest.mark.parametrize("verb", ["_get", "_put"])
@pytest.mark.parametrize("dev_type", sorted(DEVICE_CLASSES))
async def test_a_transport_failure_measures_connected_false(dev_type, verb):
    """No HTTP response at all is the one failure that says the server is gone,
    for every device type and both verbs; the caller still sees the error.

    MUTANT M1 "camera only" (the unfixed client): ``_note_link_lost``'s body
    made ``if self.dev_type == "camera": self.connected = False``: RED
    (observed), the 16 non-camera cases of 18, ``assert True is False``; 21
    of the file's 47 are red under it (those 16, the 4 below, the real
    socket). Run against the unfixed file itself, 27 are red.
    MUTANT M2 "never": ``_note_link_lost``'s body made ``pass``: RED
    (observed), all 18 cases (25 red in the file, and the camera's own case
    in ``test_w6_camera_connected_is_measured``).
    """
    dev = await _device(dev_type, _raises(httpx.ConnectError))
    try:
        with pytest.raises(httpx.ConnectError):
            await getattr(dev, verb)("x")
        assert dev.connected is False
    finally:
        await dev.conn.close()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout,
                                     httpx.RemoteProtocolError])
@pytest.mark.parametrize("verb", ["_get", "_put"])
async def test_every_kind_of_transport_failure_measures_connected_false(
        verb, failure):
    """A timeout (the server took the socket and said nothing) and a server
    that hung up mid-reply are transport failures too: the clause is on the
    family, not on one member.

    MUTANT M8 "connect errors only": the ``except httpx.TransportError:``
    clauses in ``_AlpacaDevice._get``/``_put`` narrowed to
    ``except httpx.ConnectError:``: RED (observed), all 4 cases,
    ``assert True is False``.
    """
    dev = await _device("telescope", _raises(failure))
    try:
        with pytest.raises(failure):
            await getattr(dev, verb)("x")
        assert dev.connected is False
    finally:
        await dev.conn.close()


@pytest.mark.parametrize("verb", ["_get", "_put"])
@pytest.mark.parametrize("dev_type", sorted(DEVICE_CLASSES))
async def test_an_answer_is_not_a_transport_failure(dev_type, verb):
    """The #16 rule stands for every device type: a server that ANSWERED, even
    to refuse, is proof the device is there, and reconnecting it would tear
    down a device that is merely busy. A good answer returns its value.

    MUTANT M3 "an answer is a lost link": the ``except AlpacaReplyError``
    arms of ``_AlpacaDevice._get``/``_put`` also call ``_note_link_lost()``:
    RED (observed), all 18 cases, ``assert False is True``.
    MUTANT M7 "any answer": ``_note_not_connected``'s test made ``if True:``:
    RED (observed), all 18 cases, ``assert False is True``.
    """
    refusing = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if refusing["on"]:
            return httpx.Response(200, json=_json(None, 0x500, "x"))
        return httpx.Response(200, json=_json(7))

    dev = await _device(dev_type, handler)
    try:
        assert await getattr(dev, verb)("x") == 7
        assert dev.connected is True

        refusing["on"] = True
        with pytest.raises(AlpacaReplyError):
            await getattr(dev, verb)("x")
        assert dev.connected is True
    finally:
        await dev.conn.close()


async def test_a_server_that_vanishes_measures_connected_false():
    """The issue's own picture, over a real socket: a mount answers, its Alpaca
    server goes away, and the next read is the first to say so. Nothing is
    mocked below the device class.

    MUTANT M1 (see above): RED (observed), ``assert True is False``.
    """
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        body = json.dumps(_json(7)).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
            + body)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    tel = AlpacaTelescope(AlpacaConnection("127.0.0.1", port), 0, "Fictional")
    try:
        tel.connected = True
        assert await tel._get("declination") == 7
        assert tel.connected is True

        server.close()
        await server.wait_closed()
        with pytest.raises(httpx.TransportError):
            await tel._get("declination")
        assert tel.connected is False
    finally:
        await tel.conn.close()


# --------------------------------------------------------------------------
# #989: the camera's image download is a direct GET, not a ``_get``
# --------------------------------------------------------------------------
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout,
                                     httpx.RemoteProtocolError])
async def test_a_transport_failure_on_the_image_download_measures_false(failure):
    """``_download_image`` reads through the client directly, so the camera's
    own measurement never saw it: a server that vanished between ``imageready``
    and the download left the camera 'connected'. A ReadTimeout is the likeliest
    failure on a large image, so the clause is pinned on the family, not on
    the connect error alone.

    MUTANT M4 "download unguarded": the ``try`` / ``except httpx.TransportError``
    around the GET in ``AlpacaCamera._download_image`` removed: RED (observed),
    ``assert True is False`` (all cases).
    MUTANT R1 "download connect errors only": that clause narrowed to
    ``except httpx.ConnectError:``: RED (observed), the ReadTimeout and
    RemoteProtocolError cases.
    """
    cam = await _device("camera", _raises(failure))
    try:
        with pytest.raises(failure):
            await cam._download_image()
        assert cam.connected is False
    finally:
        await cam.conn.close()


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout,
                                     httpx.RemoteProtocolError])
async def test_an_exposure_whose_download_finds_no_server_measures_false(failure):
    """The same, through ``expose``: the exposure runs, the image is ready, and
    the download is the call that finds the server gone.

    MUTANT M4 and R1 (see above): RED (observed), ``assert True is False`` and
    the narrowed clause letting the ReadTimeout and RemoteProtocolError through
    unmeasured.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        route = request.url.path.rsplit("/", 1)[-1]
        if route == "imagearray":
            raise failure("no answer", request=request)
        return httpx.Response(200, json=_json(True))

    cam = _camera_that_has_read_its_sensor(await _device("camera", handler))
    try:
        with pytest.raises(failure):
            await cam.expose(0.01, 100, 30, binning=1)
        assert cam.connected is False
        assert cam._exposing is False
    finally:
        await cam.conn.close()


# --------------------------------------------------------------------------
# #990: an ImageBytes header carrying NotConnected
# --------------------------------------------------------------------------
async def test_an_imagebytes_not_connected_measures_connected_false():
    """The header's error number is the server's answer, the same word the JSON
    arm carries in its body; read as a typed error it reaches
    ``_note_not_connected``. The driver's words after the header are never
    quoted.

    MUTANT M5 "plain DeviceError": ``_parse_imagebytes`` made to raise
    ``DeviceError(f"ImageBytes error {err_no}")`` (the unfixed code): RED
    (observed), ``AlpacaReplyError`` not raised (the plain error is a
    ``DeviceError``, not the typed one); 4 red in the file, the two cases
    below among them.
    MUTANT M6 "unguarded": the ``try`` / ``except AlpacaReplyError`` in
    ``_download_image`` moved off the ImageBytes arm: RED (observed),
    ``assert True is False``.
    """
    cam = await _device("camera", lambda r: _imagebytes(NOT_CONNECTED))
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await cam._download_image()
        assert info.value.error_number == NOT_CONNECTED
        assert cam.connected is False
        assert "0x407" in str(info.value)
        assert DRIVER_WORDS not in str(info.value)
    finally:
        await cam.conn.close()


async def test_an_exposure_whose_imagebytes_says_not_connected_measures_false():
    """The same, through ``expose``: the image is ready, the download answers
    NotConnected in its header, and the exposure fails with it.

    MUTANT M5 (see above): RED (observed), the typed error is not raised.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        route = request.url.path.rsplit("/", 1)[-1]
        if route == "imagearray":
            return _imagebytes(NOT_CONNECTED)
        return httpx.Response(200, json=_json(True))

    cam = _camera_that_has_read_its_sensor(await _device("camera", handler))
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await cam.expose(0.01, 100, 30, binning=1)
        assert info.value.error_number == NOT_CONNECTED
        assert cam.connected is False
        assert cam._exposing is False
    finally:
        await cam.conn.close()


@pytest.mark.parametrize("number", [0x408, 0x500])
async def test_every_other_imagebytes_error_is_still_proof_of_presence(number):
    """The #16 rule on the binary arm: a camera that refuses one image is a
    camera that is there. The error stays a ``DeviceError`` carrying its number.

    MUTANT M7 "any answer": ``_note_not_connected``'s test made ``if True:``:
    RED (observed), both cases, ``assert False is True``.
    """
    cam = await _device("camera", lambda r: _imagebytes(number))
    try:
        with pytest.raises(DeviceError) as info:
            await cam._download_image()
        assert isinstance(info.value, AlpacaReplyError)
        assert info.value.error_number == number
        assert "ImageBytes error" in str(info.value)
        assert DRIVER_WORDS not in str(info.value)
        assert cam.connected is True
    finally:
        await cam.conn.close()
