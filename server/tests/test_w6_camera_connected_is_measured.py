# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-47 (#16): camera `connected` is MEASURED, not remembered.

Before this fix, every camera backend's ``connected`` was a plain flag set
True by ``connect()`` and False only by an explicit ``disconnect()`` --
nothing in between ever touched it. A camera that dropped mid-run (a yanked
USB cable, a crashed Alpaca server) kept reading 'connected' forever, so
``sequence/engine.py._reconnect_gate`` -- which skips any role where
``getattr(dev, "connected", False)`` is true -- never saw it and never
reconnected it. That is the half of #16 left open after the sibling fixes:
#15 closed the seam that let a stale `connected` route a guide preview onto a
busy sensor, and the no-progress silence check
(``test_a_silent_camera_is_a_dropped_camera.py``) catches a camera that has
gone quiet across several expected frames. Neither one touches the flag
itself.

The shape mirrors ``zwo_am5.py``'s fix for the exact same bug on the mount's
serial link: derive ``connected`` from real traffic instead of remembering a
historical ``connect()``. Native cameras (``NativeCamera``,
devices/cameras/engine.py) measure it off their own REQUIRED adapter hooks
(``start_exposure``/``image_ready``/``read_frame``/``abort`` -- every brand
must implement these for real, so a failure there is always evidence the
device dropped, never an unsupported feature); the Alpaca camera client
(``AlpacaCamera``, devices/alpaca.py) measures it off a TRANSPORT failure (no
HTTP response at all), as opposed to a ``DeviceError`` -- which means the
driver DID answer, just refused the call.

Built against the simulator and fakes only, per backlog ruling D-10
(owner-approved 2026-09-30): the astrotown profile's ``reconnect_resume``
flip and the daytime rig check that justifies it are the orchestrator's, at
deploy -- this file proves the measurement, not the rig.
"""
from __future__ import annotations

import httpx
import numpy as np
import pytest

from astrodeck.devices.alpaca import AlpacaCamera, AlpacaConnection
from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.adapter import CameraAdapter, CameraCapabilities
from astrodeck.devices.cameras.engine import NativeCamera

# ===========================================================================
# NativeCamera (ZWO ASI / Player One, via devices/cameras/engine.py)
# ===========================================================================


class _FlakyAdapter(CameraAdapter):
    """A camera adapter that fails exactly ONE named call, so a test proves
    that one bad answer -- not a batch of them -- is what flips `connected`.
    Modeled on test_camera_engine.py's FakeAdapter.

    ``never_ready=True`` makes ``image_ready`` always answer False instead of
    raising, for the imageready-TIMEOUT case (a camera that stays silent
    rather than one that answers with an error)."""

    def __init__(self, *, fail_on: str | None = None,
                 exc: Exception | None = None,
                 never_ready: bool = False,
                 read_modes: tuple[str, ...] = ()):
        self.fail_on = fail_on
        self.exc = exc if exc is not None else DeviceError("adapter call failed")
        self.never_ready = never_ready
        self._read_modes = read_modes
        self.calls: list[str] = []

    def _mark(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_on:
            raise self.exc

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            sensor_width=4, sensor_height=2, pixel_size_um=3.76, bit_depth=16,
            bayer_pattern=None, gain_range=(0, 600), offset_range=(0, 255),
            bin_modes=(1,), roi_supported=True, has_cooler=False,
            has_dew_heater=False, max_adu=65535, read_modes=self._read_modes)

    def open(self, index: int) -> None:
        self._mark("open")

    def close(self) -> None:
        self._mark("close")

    def start_exposure(self, *, seconds, gain, offset, roi, light) -> None:
        self._mark("start_exposure")

    def image_ready(self) -> bool:
        self._mark("image_ready")
        return not self.never_ready

    def read_frame(self) -> bytes:
        self._mark("read_frame")
        return np.zeros(4 * 2, dtype="<u2").tobytes()

    def abort(self) -> None:
        self._mark("abort")

    def set_read_mode(self, mode: str) -> None:
        self._mark("set_read_mode")


async def test_a_required_hook_failure_measures_connected_false():
    """The core case: `image_ready` -- a CameraAdapter abstractmethod every
    brand must implement for real -- fails mid-exposure, and `connected` must
    go false right there instead of waiting for a `disconnect()` nobody calls.

    Named mutant: ``NativeCamera._poll``'s ``except Exception:
    self.connected = False`` changed to ``except Exception: pass``. FAILS:
    ``assert cam.connected is False`` after the raised DeviceError -- the
    flag stays True exactly as it did before this fix, the 2026-09-12 shape
    of #16.
    """
    fa = _FlakyAdapter(fail_on="image_ready")
    cam = NativeCamera(fa)
    await cam.connect()
    assert cam.connected is True, "precondition"

    with pytest.raises(DeviceError):
        await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is False, (
        "a required hook's failure must measure `connected` false -- a "
        "camera that just refused to answer `image_ready` is not one the "
        "reconnect gate should keep skipping")


async def test_start_exposure_failure_also_measures_connected_false():
    """`start_exposure` is the other required hook on the hot path (the lock-
    serialized side, via `_run`, not `_poll`) -- both halves of the split
    documented in NativeCamera's module docstring must measure the same way.

    Named mutant: ``_run``'s ``if measures_connected: self.connected =
    False`` changed to ``if False:``. FAILS: ``assert cam.connected is
    False`` -- the default flip never fires for ANY `_run` caller, which
    would also silently re-break `set_cooler`/`set_target_temp`/
    `set_dew_heater` (all pre-checked, all supposed to measure).
    """
    fa = _FlakyAdapter(fail_on="start_exposure")
    cam = NativeCamera(fa)
    await cam.connect()

    with pytest.raises(DeviceError):
        await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is False


async def test_unsupported_read_mode_does_not_measure_connected_false():
    """The discriminator the whole design rests on: `set_read_mode` is the
    ONE `_run` caller with no capability pre-check (no `has_read_modes`
    exists), so a brand that doesn't support read modes raising here is
    business as usual, not a dropped camera -- `connected` must stay True.

    Named mutant: the ``measures_connected=False`` argument removed from
    `expose()`'s `set_read_mode` call (so it falls back to the default
    True). FAILS: ``assert cam.connected is True`` -- a camera with no
    selectable read modes would appear to drop out on every single exposure
    that names one, and the reconnect gate would close+reopen a perfectly
    healthy handle in a loop.
    """
    fa = _FlakyAdapter(fail_on="set_read_mode",
                       exc=DeviceError("camera has no selectable read modes"))
    cam = NativeCamera(fa)
    await cam.connect()

    with pytest.raises(DeviceError, match="no selectable read modes"):
        await cam.expose(0.01, gain=0, offset=0, read_mode="LowNoise")

    assert cam.connected is True, (
        "an unsupported-feature DeviceError must not be read as the device "
        "dropping out")


async def test_imageready_timeout_measures_connected_false():
    """A camera that goes SILENT (never answers ready) is the same evidence
    as one that answers with an error -- just raised locally once the
    exposure's own deadline passes, rather than thrown by the adapter.

    Named mutant: the ``self.connected = False`` line added just before
    ``raise DeviceError("exposure imageready timeout")`` in `expose()`
    deleted. FAILS: ``assert cam.connected is False`` -- a camera stuck mid-
    exposure would time out this one call yet still read 'connected'
    afterward.
    """
    fa = _FlakyAdapter(never_ready=True)
    cam = NativeCamera(fa)
    await cam.connect()
    cam.EXPOSURE_POLL_MARGIN_S = 0.0   # instance override: fire immediately

    with pytest.raises(DeviceError, match="imageready timeout"):
        await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is False


async def test_a_healthy_exposure_leaves_connected_true():
    """Sanity control (not independently mutated -- it exists to catch a
    version that flips `connected` unconditionally, the mirror image of
    every test above). A camera that answers every call correctly must stay
    'connected' through a whole exposure, not just until the first poll."""
    fa = _FlakyAdapter()
    cam = NativeCamera(fa)
    await cam.connect()

    frame = await cam.expose(0.01, gain=0, offset=0)

    assert cam.connected is True
    assert frame.data.shape == (2, 4)


# ===========================================================================
# AlpacaCamera (devices/alpaca.py)
# ===========================================================================


class _ScriptedConnection(AlpacaConnection):
    """An AlpacaConnection double whose GET/PUT replies are scripted per
    Alpaca method name, so a test can fail exactly one call without faking a
    real HTTP response. ``AlpacaConnection.__init__`` still runs (so
    ``self.http`` exists, unused) -- matches test_alpaca_imageready_timeout.py's
    ``_stub_camera`` pattern of never actually dialing anything."""

    def __init__(self, *, fail: dict[str, Exception] | None = None,
                 replies: dict[str, object] | None = None):
        super().__init__("127.0.0.1", 11111)
        self._fail = dict(fail or {})
        self._replies = dict(replies or {})
        self.calls: list[str] = []

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(method)
        if method in self._fail:
            raise self._fail[method]
        return self._replies.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(method)
        if method in self._fail:
            raise self._fail[method]
        return self._replies.get(method)


def _stub_alpaca_camera(conn: AlpacaConnection) -> AlpacaCamera:
    cam = AlpacaCamera(conn, 0, "stub")
    cam.sensor_width = 4
    cam.sensor_height = 2
    cam.max_gain = 0
    return cam


async def test_alpaca_transport_failure_measures_connected_false():
    """The Alpaca equivalent of the native case above: the driver never
    answers at all (connection refused -- the server process is gone, the
    exact #16 incident shape for a network camera).

    Named mutant: ``AlpacaCamera._put``'s ``except httpx.TransportError:
    self.connected = False`` changed to ``except httpx.TransportError:
    pass``. FAILS: ``assert cam.connected is False`` -- the reconnect gate
    would keep skipping a camera whose Alpaca server has vanished.
    """
    conn = _ScriptedConnection(
        fail={"startexposure": httpx.ConnectError("connection refused")})
    cam = _stub_alpaca_camera(conn)
    cam.connected = True
    assert cam.connected is True, "precondition"

    with pytest.raises(httpx.ConnectError):
        await cam.expose(0.01, 100, 30, binning=1)

    assert cam.connected is False


async def test_alpaca_device_error_does_not_measure_connected_false():
    """The discriminator for Alpaca: a ``DeviceError`` means the driver
    ANSWERED (a non-200 status, or an ASCOM ``ErrorNumber`` -- 'exposure
    already in progress', say) -- proof the device is there, just refusing
    this one call. That must not read as a drop.

    Named mutant: the ``except httpx.TransportError:`` clauses in
    `AlpacaCamera._get`/`_put` widened to bare ``except Exception:``. FAILS:
    ``assert cam.connected is True`` -- a camera that merely declined one
    command (busy, bad parameter) would be torn down and reconnected for no
    reason.
    """
    conn = _ScriptedConnection(
        fail={"startexposure": DeviceError("exposure already in progress")})
    cam = _stub_alpaca_camera(conn)
    cam.connected = True

    with pytest.raises(DeviceError, match="already in progress"):
        await cam.expose(0.01, 100, 30, binning=1)

    assert cam.connected is True


async def test_a_healthy_alpaca_exposure_leaves_connected_true(monkeypatch):
    """Sanity control, mirroring the native one above: a normal exposure over
    a healthy Alpaca link must leave `connected` alone."""
    conn = _ScriptedConnection(replies={"imageready": True})
    cam = _stub_alpaca_camera(conn)
    cam.connected = True

    async def _download_image():
        return np.zeros((2, 4), dtype=np.uint16)

    monkeypatch.setattr(cam, "_download_image", _download_image)

    frame = await cam.expose(0.01, 100, 30, binning=1)

    assert cam.connected is True
    assert frame.data.shape == (2, 4)
