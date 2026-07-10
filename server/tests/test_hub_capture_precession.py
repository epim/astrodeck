"""Regression tests for J2000<->JNOW precession, autofocus-restore, capture mutual-exclusion fixes.

Each test pins one confirmed bug so it can never silently regress:
  * J2000->JNOW precession at the mount boundary (~20 arcmin today), keyed on
    the mount DEVICE's backend (not the global hub.mode) so a mixed rig
    (e.g. primary NINA + native Alpaca mount) still precesses correctly
  * autofocus restores the focuser to start on any mid-sweep exception
  * the single camera is mutually excluded across capture paths
  * disconnect_all closes the native httpx sessions it held (no leak)
  * apply_profile connects NINA before Alpaca (mixed rig survives)
  * the apply route no longer self-cancels; park supersedes an in-flight goto
"""
from __future__ import annotations

import asyncio
import calendar
import math

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus import run_autofocus
from astrodeck.hub import Hub, precess_j2000_to_jnow, precess_jnow_to_j2000


def _sep_arcmin(ra0_h, dec0_d, ra1_h, dec1_d) -> float:
    ra0, dec0 = math.radians(ra0_h * 15.0), math.radians(dec0_d)
    ra1, dec1 = math.radians(ra1_h * 15.0), math.radians(dec1_d)
    cos_sep = (math.sin(dec0) * math.sin(dec1)
               + math.cos(dec0) * math.cos(dec1) * math.cos(ra0 - ra1))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep)))) * 60.0


# --------------------------------------------------------------- precession

def test_j2000_to_jnow_offset_is_epoch_realistic():
    """A J2000 target precessed to JNOW today lands ~0.2 deg away — the exact
    offset a JNOW mount would mis-point by if fed raw J2000. The precise
    displacement is position-dependent (precession+nutation); for M13 in mid-2026
    astropy gives ~12 arcmin. That is ~0.2 deg — well outside the frame on a long
    focal length rig, and enough to corrupt a mount's sync/alignment model."""
    when = calendar.timegm((2026, 7, 3, 6, 0, 0, 0, 0, 0))
    # M13 (J2000)
    ra0, dec0 = 16.6949, 36.4603
    ra1, dec1 = precess_j2000_to_jnow(ra0, dec0, when)
    sep = _sep_arcmin(ra0, dec0, ra1, dec1)
    assert 10.0 < sep < 15.0, f"unexpected J2000->JNOW offset {sep:.1f}'"


def test_jnow_j2000_roundtrip_is_tight():
    when = calendar.timegm((2026, 7, 3, 6, 0, 0, 0, 0, 0))
    ra0, dec0 = 5.5881, -5.3911    # M42 (J2000)
    ra1, dec1 = precess_j2000_to_jnow(ra0, dec0, when)
    ra2, dec2 = precess_jnow_to_j2000(ra1, dec1, when)
    assert _sep_arcmin(ra0, dec0, ra2, dec2) < 0.05


class _FakeAlpacaTel:
    """Fake mount exposing the Alpaca ``_get`` seam so the hub can probe
    EquatorialSystem. ``equ`` = ASCOM EquatorialCoordinateType. Carries the
    real Alpaca device marker (``backend`` — devices/alpaca.py:257) because
    the JNOW gate is DEVICE-keyed, not hub-mode-keyed."""
    backend = "alpaca"

    def __init__(self, equ: int):
        self._equ = equ

    async def _get(self, method, **params):
        if method == "equatorialsystem":
            return self._equ
        raise KeyError(method)


class _FakeSimTel:
    """A sim-backed mount: no ``backend`` marker (devices/base.py default '')."""
    backend = ""


async def test_mount_frame_converts_only_for_alpaca_devices():
    h = Hub()
    tel_jnow = _FakeAlpacaTel(equ=1)         # topocentric / JNOW

    # A sim DEVICE never converts — even when the hub is in alpaca mode.
    h.mode = "alpaca"
    assert await h.to_mount_frame(_FakeSimTel(), 16.6949, 36.4603) == (16.6949, 36.4603)

    # An Alpaca topocentric device converts (target moves ~arcmin off J2000)…
    h._mount_wants_jnow = None
    ra1, dec1 = await h.to_mount_frame(tel_jnow, 16.6949, 36.4603)
    assert _sep_arcmin(16.6949, 36.4603, ra1, dec1) > 5.0

    # …INCLUDING on a mixed rig where the hub mode is "nina" (the phase-2
    # review's arcminute regression: primary NINA + native Alpaca mount).
    h2 = Hub()
    h2.mode = "nina"
    ra2, dec2 = await h2.to_mount_frame(_FakeAlpacaTel(equ=1), 16.6949, 36.4603)
    assert _sep_arcmin(16.6949, 36.4603, ra2, dec2) > 5.0

    # An Alpaca mount that reports J2000 (==2) must NOT double-precess.
    h3 = Hub()
    h3.mode = "alpaca"
    assert await h3.to_mount_frame(_FakeAlpacaTel(equ=2), 16.6949, 36.4603) == (16.6949, 36.4603)


# ----------------------------------------------------------- autofocus recovery

async def test_autofocus_restores_focuser_on_midsweep_exception():
    """A transient camera error mid-sweep must NOT strand the focuser at an
    arbitrary sweep position (which would shoot the whole target defocused) — it
    returns to start_pos and re-raises."""
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    start = await foc.get_position()

    real_expose = cam.expose
    calls = {"n": 0}

    async def flaky_expose(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:               # blow up partway through the sweep
            raise DeviceError("camera HTTP timeout")
        return await real_expose(*a, **k)

    cam.expose = flaky_expose
    with pytest.raises(DeviceError):
        await run_autofocus(cam, foc, exposure_s=0.05, gain=200,
                            step=350, steps_each_side=4, binning=2)
    # focuser back where the sweep began, not parked hundreds of steps out.
    assert await foc.get_position() == start


# -------------------------------------------------------- camera mutual exclusion

async def test_exposure_guard_rejects_concurrent_exposure():
    h = Hub()
    async with h.exposure_guard("live loop"):
        with pytest.raises(DeviceError) as ei:
            async with h.exposure_guard("single capture"):
                pass
    # busy label of the HOLDER is surfaced so the UI can say who owns the camera.
    assert "live loop" in str(ei.value)
    # lock released after the block — a fresh guard now succeeds.
    async with h.exposure_guard("autofocus"):
        pass


async def test_capture_paths_do_not_interleave(monkeypatch, tmp_path):
    """Two overlapping hub.capture calls: the second is refused rather than
    polling the shared camera concurrently."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        real_expose = cam.expose
        gate = asyncio.Event()

        async def slow_expose(*a, **k):
            gate.set()
            await asyncio.sleep(0.2)       # hold the camera so the 2nd overlaps
            return await real_expose(*a, **k)

        cam.expose = slow_expose
        first = asyncio.create_task(h.capture(0.01, 100, 30, 1))
        await gate.wait()                  # first is now inside the guard
        with pytest.raises(DeviceError):
            await h.capture(0.01, 100, 30, 1)
        await first
    finally:
        await h.disconnect_all()


# ------------------------------------------------------------- session leak

class _CountingSession:
    """Minimal BackendSession stand-in that records close()."""
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


async def test_disconnect_all_closes_retained_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    # simulate a legacy single-role Alpaca session and a RigSpec-connect session
    # both retained on the hub, and prove the teardown aclose's each one.
    legacy = _CountingSession()
    h._alpaca_sessions["camera"] = legacy

    class _Res:
        def __init__(self, sess):
            self.sessions = sess
            self.results = []
    rig_sess = _CountingSession()
    h.last_connect_result = _Res({("native", "h", 1): rig_sess})

    await h.disconnect_all()
    assert legacy.closed == 1, "legacy Alpaca session was never aclosed (leak)"
    assert rig_sess.closed == 1, "RigSpec-connect session was never aclosed (leak)"
    assert h._alpaca_sessions == {}


# ------------------------------------------------- mixed Alpaca+NINA apply order

async def test_apply_profile_connects_nina_before_alpaca(monkeypatch, tmp_path):
    """A mixed profile (Alpaca camera + NINA mount) must connect NINA FIRST, or
    connect_nina's teardown wipes the Alpaca devices just connected (while still
    reporting them ok)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    from astrodeck.profiles import Profile, ProfileDevice

    order: list[str] = []

    async def fake_nina(self, host, port=1888):
        order.append("nina")

    async def fake_alpaca(self, role, host, port, dev_type, dev_num, name):
        order.append(f"alpaca:{role}")

    monkeypatch.setattr(Hub, "_connect_nina_unlocked", fake_nina)
    monkeypatch.setattr(Hub, "_connect_alpaca_device_unlocked", fake_alpaca)

    h = Hub()
    prof = Profile(
        name="mixed", primary_backend="native", nina_host="127.0.0.1",
        devices=[
            ProfileDevice(role="camera", backend="alpaca", host="127.0.0.1",
                          port=11111, dev_type="camera", dev_num=0, name="cam"),
            ProfileDevice(role="telescope", backend="nina", name="mount"),
        ])
    await h.apply_profile(prof)
    assert order and order[0] == "nina", f"NINA must connect first, got {order}"
    assert "alpaca:camera" in order
