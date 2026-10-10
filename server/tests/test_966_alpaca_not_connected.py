# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#966: an Alpaca server that answers NotConnected (0x407) has said the device
is not there, and the client believes it.

After #937 the comhost answers HTTP 200 / ErrorNumber 0x407 for a request to a
device that was never connected or that was fault-evicted and rebuilt empty.
The client treated every answered error as proof of presence (#16) and only a
transport failure on the CAMERA measured ``connected`` false, so an evicted
device kept reading 'connected', was polled forever, and the reconnect gate
(``sequence/engine.py._reconnect_gate``) never fired.

Now every Alpaca device type measures it: an ``AlpacaReplyError`` carrying 0x407
sets ``connected`` false. Every other answered error still leaves it alone.

Reading the gate's branch once it could fire found a second defect, in the hub:
``connect_alpaca_device`` replaced the old device object with a new one and then
called ``old.disconnect()``, whose Connected=False reaches the SAME device slot
the new object had just connected. ``reconnect_role`` reported success, and the
new object's first read answered 0x407 again. The hub now leaves the old object
connected when the new one addresses the same Alpaca device.

No comtypes and no hardware: fake COM objects behind the real ComDevice, the
real comhost server, and the real Alpaca client. Each test names the mutant of
production code it was shown red under (applied to a byte copy of
``devices/alpaca.py`` or ``hub.py``, restored from that copy and md5-checked).
"""
from __future__ import annotations

import threading

import httpx
import pytest

import astrodeck.comhost.handlers_camera as handlers_camera
import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.comhost.device import ComDevice
from astrodeck.config import AppConfig, ConfigStore, EscalationConfig, SafetyConfig, Site
from astrodeck.devices.alpaca import (
    DEVICE_CLASSES,
    AlpacaCamera,
    AlpacaConnection,
    AlpacaReplyError,
    AlpacaTelescope,
)
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

NOT_CONNECTED = 0x407


def _not_connected() -> AlpacaReplyError:
    return AlpacaReplyError(
        "Alpaca error 0x407 on camera/0/x: the driver's message is not quoted",
        http_status=200, error_number=NOT_CONNECTED)


class _Answers:
    """A connection double whose every GET and PUT raises ``error``."""

    host, port = "127.0.0.1", 11111

    def __init__(self, error: Exception):
        self.error = error

    async def get(self, dev_type, dev_num, method, **params):
        raise self.error

    async def put(self, dev_type, dev_num, method, **params):
        raise self.error


# --------------------------------------------------------------------------
# The measurement, on every device type the client has
# --------------------------------------------------------------------------
@pytest.mark.parametrize("verb", ["_get", "_put"])
@pytest.mark.parametrize("dev_type", sorted(DEVICE_CLASSES))
async def test_a_not_connected_answer_measures_connected_false(dev_type, verb):
    """The answer is an exception the caller still sees, and the flag the
    reconnect gate reads goes false, for every device type and both verbs.

    MUTANT M1 "never": the ``if exc.error_number == _ASCOM_NOT_CONNECTED:``
    test in ``_AlpacaDevice._note_not_connected`` made ``if False:`` (the
    unfixed client): RED (observed), all 18 cases, ``assert True is False``;
    22 of the file's 36 are red under it.
    MUTANT M2 "wrong number": ``_ASCOM_NOT_CONNECTED`` set to 0x408: RED
    (observed), all 18 cases (24 red in the file).
    MUTANT M4 "camera only": the handling moved out of the base class into
    ``AlpacaCamera._get``/``_put``: RED (observed), the 16 non-camera cases
    (17 red in the file, with the mount round trip below).
    """
    error = _not_connected()
    dev = DEVICE_CLASSES[dev_type](_Answers(error), 0, "stub")
    dev.connected = True

    with pytest.raises(AlpacaReplyError) as info:
        await getattr(dev, verb)("x")

    assert info.value is error  # the caller still gets the answer, unchanged
    assert dev.connected is False


@pytest.mark.parametrize("error", [
    pytest.param(AlpacaReplyError("busy", http_status=200, error_number=0x408),
                 id="invalid-while-parked"),
    pytest.param(AlpacaReplyError("nope", http_status=200, error_number=0x400),
                 id="not-implemented"),
    pytest.param(AlpacaReplyError("driver", http_status=200, error_number=0x500),
                 id="driver-error"),
    pytest.param(AlpacaReplyError("5xx", http_status=500, error_number=None),
                 id="http-500"),
    pytest.param(AlpacaReplyError("unreadable", http_status=200,
                                  error_number=None),
                 id="unreadable-number"),
    pytest.param(DeviceError("exposure already in progress"),
                 id="plain-device-error"),
])
@pytest.mark.parametrize("verb", ["_get", "_put"])
async def test_every_other_answered_error_is_still_proof_of_presence(error, verb):
    """The #16 rule stands for everything but NotConnected: a device that
    refuses one call is a device that is there, and reconnecting it would tear
    down a camera that is merely busy.

    MUTANT M3 "any answer": ``_note_not_connected``'s test made ``if True:``:
    RED (observed), the 10 cases that raise an AlpacaReplyError,
    ``assert False is True`` (the plain DeviceError never reaches it; 14 red
    in the file, the round trips among them).
    """
    dev = AlpacaTelescope(_Answers(error), 0, "stub")
    dev.connected = True

    with pytest.raises(DeviceError):
        await getattr(dev, verb)("x")

    assert dev.connected is True


async def test_a_not_connected_json_image_download_measures_connected_false():
    """``_download_image`` reads the JSON ImageArray through the connection
    directly, not through ``_get``, and it is the read the comhost serves. A
    0x407 there must be measured too.

    MUTANT M5 "unwrap unguarded": the ``try`` / ``except AlpacaReplyError`` in
    ``AlpacaCamera._download_image`` removed: RED (observed),
    ``assert True is False``.
    """
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "Value": None, "ErrorNumber": NOT_CONNECTED,
            "ErrorMessage": "camera #0 is not connected"})

    conn = AlpacaConnection("127.0.0.1", 11111)
    await conn.http.aclose()
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    cam = AlpacaCamera(conn, 0, "stub")
    cam.connected = True
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await cam._download_image()
        assert info.value.error_number == NOT_CONNECTED
        assert cam.connected is False
    finally:
        await conn.close()


# --------------------------------------------------------------------------
# Through a running comhost: evict, flips to false, reconnect, reads work
# --------------------------------------------------------------------------
class _Cam:
    """A fake COM camera. ``ImageReady`` blocks (past the host's deadline) while
    ``wedge`` is set, which is what gets the device fault-evicted."""

    Connected = False
    CameraXSize = 4
    CameraYSize = 2
    PixelSizeX = 3.8
    GainMax = 100
    CanSetCCDTemperature = False
    SensorType = 0
    MaxADU = 65535
    CCDTemperature = 10.0
    wedge = False
    release = threading.Event()

    @property
    def ImageReady(self):
        if _Cam.wedge:
            _Cam.release.wait(5.0)
        return True


class _Scope:
    Connected = False
    Declination = 20.0
    Tracking = True
    wedge = False
    release = threading.Event()

    @property
    def RightAscension(self):
        if _Scope.wedge:
            _Scope.release.wait(5.0)
        return 10.0


@pytest.fixture()
def comhost(monkeypatch):
    """A running comhost serving camera #0, camera #1 and telescope #0, whose
    devices fault-evict after 0.5 s. Yields ``(srv, port)``."""
    handlers_camera.register()  # undo any sibling's DEVICE_API mutation
    handlers_telescope.register()
    for cls in (_Cam, _Scope):
        cls.wedge = False
        cls.release = threading.Event()
    makers = {"Fake.Cam": _Cam, "Fake.Cam1": _Cam, "Fake.Scope": _Scope}
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: makers[progid](),
                                 timeout_s=0.5))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Camera", "camera", "Fake.Cam", "Fake Cam", 0),
        AscomDriver("Camera", "camera", "Fake.Cam1", "Fake Cam 1", 1),
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    yield srv, srv.server_address[1]
    for cls in (_Cam, _Scope):
        cls.release.set()
    srv.com_host.close()
    srv.shutdown()


async def _evict(dev, wedge_cls, method: str) -> None:
    """Wedge one read past the host's deadline so the host fault-evicts the
    device (HTTP 500, slot dropped), the way a hung driver does."""
    wedge_cls.wedge = True
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await dev._get(method)
        assert info.value.http_status == 500
    finally:
        wedge_cls.wedge = False


async def test_an_evicted_camera_reads_not_connected_then_reconnects(comhost):
    """The issue's own recipe: evict, assert ``connected`` flips to False,
    reconnect, assert reads work.

    MUTANT M1 (see above): RED (observed), ``assert True is False`` after the
    0x407 read: the unfixed client kept the evicted camera 'connected'.
    """
    _, port = comhost
    cam = AlpacaCamera(AlpacaConnection("127.0.0.1", port), 0, "Fake Cam")
    try:
        await cam.connect()
        assert await cam._get("ccdtemperature") == 10.0
        await _evict(cam, _Cam, "imageready")

        with pytest.raises(AlpacaReplyError) as info:
            await cam._get("ccdtemperature")
        assert (info.value.http_status, info.value.error_number) == (
            200, NOT_CONNECTED)
        assert cam.connected is False

        await cam.connect()
        assert cam.connected is True
        assert await cam._get("ccdtemperature") == 10.0
    finally:
        await cam.conn.close()


async def test_an_evicted_mount_reads_not_connected_then_reconnects(comhost):
    """The same for a telescope, which has no transport-failure measurement of
    its own and was never going to notice.

    MUTANT M4 (see above): RED (observed), ``assert True is False``.
    """
    _, port = comhost
    tel = AlpacaTelescope(AlpacaConnection("127.0.0.1", port), 0, "Fake Scope")
    try:
        await tel.connect()
        assert tel.connected is True
        await _evict(tel, _Scope, "rightascension")

        with pytest.raises(AlpacaReplyError) as info:
            await tel._get("declination")
        assert info.value.error_number == NOT_CONNECTED
        assert tel.connected is False

        await tel.connect()
        assert tel.connected is True
        assert await tel._get("declination") == 20.0
    finally:
        await tel.conn.close()


# --------------------------------------------------------------------------
# Through the hub and the engine's reconnect gate
# --------------------------------------------------------------------------
@pytest.fixture()
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=45.0, longitude=-116.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "RECONNECT_BACKOFF_S", 0.01)
    # The status loop reads the camera on its own clock; a read queued behind
    # the wedge would race this test's evictions. It is not what is under test.
    monkeypatch.setattr(Hub, "ensure_status_poller", lambda self: None)
    return store


@pytest.fixture()
async def hub(temp_store):
    h = Hub()
    yield h
    await h.disconnect_all()


def _gate_engine(hub: Hub) -> SequenceEngine:
    eng = SequenceEngine(hub)
    eng._cfg = AppConfig(
        escalation=EscalationConfig(reconnect_resume=True, reconnect_retries=1),
        safety=SafetyConfig(enabled=False))
    eng.plan = SequencePlan(
        name="gate", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="Darks", ra_hours=0, dec_deg=0, calibration=True,
                        autofocus_first=False,
                        steps=[ExposureStep(exposure_s=0.05, count=1,
                                            frame_type="Dark")])])
    return eng


async def test_the_reconnect_gate_heals_an_evicted_comhost_camera(comhost, hub):
    """End to end, the chain the issue is about: the camera is evicted, the next
    read answers 0x407, the gate sees ``connected`` false at the frame boundary,
    reconnects through the hub, and the camera the run now holds reads.

    MUTANT M1 (see above): RED (observed), the gate does nothing
    (``connected`` still true) and the role still holds the evicted camera.
    MUTANT M6 "disconnect the old one" (hub.py): the ``and not replaced_same``
    clause removed (the unfixed hub): RED (observed), ``reconnect_role``
    answers True and the NEW camera's first read raises 0x407, because the old
    object's Connected=False dropped the slot the new one had just connected.
    Only this test is red under M6.
    """
    _, port = comhost
    await hub.connect_alpaca_device("camera", "127.0.0.1", port, "camera", 0,
                                    "Fake Cam")
    old = hub.devices["camera"]
    await _evict(old, _Cam, "imageready")
    with pytest.raises(AlpacaReplyError):
        await old._get("ccdtemperature")

    await _gate_engine(hub)._reconnect_gate()

    new = hub.devices["camera"]
    assert old is not new
    assert new.connected is True
    assert await new._get("ccdtemperature") == 10.0


async def test_the_gate_leaves_a_healthy_comhost_camera_alone(comhost, hub):
    """The other half: a camera that answers is not reconnected."""
    _, port = comhost
    await hub.connect_alpaca_device("camera", "127.0.0.1", port, "camera", 0,
                                    "Fake Cam")
    before = hub.devices["camera"]
    assert await before._get("ccdtemperature") == 10.0

    await _gate_engine(hub)._reconnect_gate()

    assert hub.devices["camera"] is before


async def test_replacing_a_role_with_another_device_disconnects_the_old_one(
        comhost, hub):
    """The hub fix is narrow: only the SAME Alpaca device keeps its connection.
    A role moved to camera #1 still releases camera #0 on the server.

    MUTANT M7 "never disconnect": ``replaced_same`` made ``True``: RED
    (observed), camera #0 is still in the comhost's device table.
    """
    srv, port = comhost
    await hub.connect_alpaca_device("camera", "127.0.0.1", port, "camera", 0,
                                    "Fake Cam")
    assert srv.com_host._devices[("camera", 0)].connected is True

    await hub.connect_alpaca_device("camera", "127.0.0.1", port, "camera", 1,
                                    "Fake Cam 1")

    assert srv.com_host._devices[("camera", 1)].connected is True
    assert ("camera", 0) not in srv.com_host._devices  # disconnected and dropped
