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
from pathlib import Path

import pytest
from astropy.io import fits

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimTelescope, build_sim_rig
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


# -------------------------------------------- UX-13: status publishes J2000 RA/Dec

class _StatusAlpacaTel(SimTelescope):
    """A connected sim mount dressed as a real Alpaca device: carries the
    ``backend='alpaca'`` marker and the ``_get`` EquatorialSystem seam so the
    hub's JNOW gate fires, and reports a FIXED FICTIONAL position. Subclasses
    SimTelescope only to inherit the tracking/parked/slewing/rate methods
    ``poll_status``'s mount block awaits."""

    def __init__(self, ra_hours: float, dec_deg: float, *, equ: int):
        from astrodeck.devices.sim import SimRig
        super().__init__(SimRig())
        self.backend = "alpaca"
        self._equ = equ
        self._ra, self._dec = ra_hours, dec_deg
        self.connected = True

    async def _get(self, method, **params):
        if method == "equatorialsystem":
            return self._equ
        raise KeyError(method)

    async def get_position(self) -> tuple[float, float]:
        return self._ra, self._dec


async def test_poll_status_publishes_j2000_from_jnow_mount():
    """UX-13: the hub status builder must run the mount's reported position back
    through ``from_mount_frame`` so a real Alpaca (JNOW) mount publishes canonical
    J2000 RA/Dec — Atlas survey tiles, the FOV overlay and Send-to-Plan all
    consume ``mount.ra_hours``/``dec_deg`` as J2000. FICTIONAL coords only."""
    ra_jnow, dec_jnow = 7.7777, 22.2222        # FICTIONAL JNOW report

    # (1) Alpaca topocentric (JNOW, equ==1): published coords are precessed to J2000.
    h = Hub()
    h.devices["telescope"] = _StatusAlpacaTel(ra_jnow, dec_jnow, equ=1)
    m = (await h.poll_status())["mount"]
    exp_ra, exp_dec = precess_jnow_to_j2000(ra_jnow, dec_jnow)
    assert m["ra_hours"] == pytest.approx(exp_ra, abs=1e-3)
    assert m["dec_deg"] == pytest.approx(exp_dec, abs=1e-3)
    # …and it genuinely moved off the raw JNOW report (from_mount_frame applied).
    assert _sep_arcmin(ra_jnow, dec_jnow, m["ra_hours"], m["dec_deg"]) > 5.0

    # (2) An Alpaca mount that reports J2000 (equ==2) is left untouched (no-op).
    h2 = Hub()
    h2.devices["telescope"] = _StatusAlpacaTel(ra_jnow, dec_jnow, equ=2)
    m2 = (await h2.poll_status())["mount"]
    assert m2["ra_hours"] == ra_jnow and m2["dec_deg"] == dec_jnow

    # (3) A native/sim mount (backend "") is likewise unchanged.
    from astrodeck.devices.sim import SimRig
    h3 = Hub()
    tel = SimTelescope(SimRig())
    await tel.connect()
    tel.rig.ra_hours, tel.rig.dec_deg = ra_jnow, dec_jnow
    h3.devices["telescope"] = tel
    m3 = (await h3.poll_status())["mount"]
    assert m3["ra_hours"] == ra_jnow and m3["dec_deg"] == dec_jnow


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


async def test_capture_frame_type_controls_shutter(monkeypatch, tmp_path):
    """UX-22: Dark AND Bias are shutter-closed (light=False); Light and Flat
    expose the sensor (light=True). The step editor now scripts all four, so the
    shutter must follow frame_type."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        real_expose = cam.expose
        seen: dict[str, object] = {}

        async def spy_expose(*a, **k):
            seen["light"] = k.get("light")
            return await real_expose(*a, **k)

        cam.expose = spy_expose
        for ft, expect_light in [("Light", True), ("Flat", True),
                                 ("Dark", False), ("Bias", False)]:
            await h.capture(0.01, 100, 30, 1, frame_type=ft)
            assert seen["light"] is expect_light, f"{ft} -> light={seen['light']}"
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


# ------------------------------------------------- FITS header completeness (T4)

async def test_capture_writes_full_headers(monkeypatch, tmp_path):
    """Caller -> save_fits wiring: config/site/device telemetry lands in the FITS."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    from astrodeck.config import Optics, Site
    cfg = temp_store.cfg()
    cfg.optics = Optics(focal_length_mm=530.0, pixel_size_um=3.76,
                        telescope_name="Askar 71F")
    cfg.site = Site(latitude=40.0, longitude=-105.0, elevation_m=1600.0,
                    is_default=False)               # INVENTED coords (privacy)
    temp_store.bump_and_save()

    h = Hub()
    await h.connect_sim()
    try:
        cam = h.devices["camera"]
        await cam.set_cooler(True, -10.0)           # so SET-TEMP is present
        await h.capture(1.0, 100, 30, binning=2, save=True, target="M42")
        saved = Path(h.last_frame.saved_path)
        with fits.open(saved) as hdul:
            hd = hdul[0].header
        assert hd["TELESCOP"] == "Askar 71F"
        assert hd["FOCALLEN"] == pytest.approx(530.0)
        assert hd["XPIXSZ"] == pytest.approx(3.76 * 2)
        assert hd["INSTRUME"]
        assert hd["SET-TEMP"] == pytest.approx(-10.0)
        assert hd["SITELAT"] == pytest.approx(40.0)
        assert hd["SITELONG"] == pytest.approx(-105.0)
        assert "FOCPOS" in hd                        # sim focuser present
        assert "ROTATANG" in hd                      # sim rotator present
        assert hd["EQUINOX"] == pytest.approx(2000.0)
        assert hd["RADESYS"] == "ICRS"
    finally:
        await h.disconnect_all()


async def test_capture_radec_written_as_j2000(monkeypatch, tmp_path):
    """A JNOW Alpaca mount's reported position is precessed to J2000 before it is
    written, and EQUINOX=2000.0 pairs it (supervisor ruling 3). FICTIONAL coords."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    try:
        ra_jnow, dec_jnow = 7.7777, 22.2222
        h.devices["telescope"] = _StatusAlpacaTel(ra_jnow, dec_jnow, equ=1)
        await h.capture(0.5, 100, 30, 1, save=True)
        saved = Path(h.last_frame.saved_path)
        with fits.open(saved) as hdul:
            hd = hdul[0].header
        exp_ra, exp_dec = precess_jnow_to_j2000(ra_jnow, dec_jnow)
        assert hd["RA"] == pytest.approx(exp_ra * 15.0, abs=1e-2)
        assert hd["DEC"] == pytest.approx(exp_dec, abs=1e-2)
        assert hd["EQUINOX"] == pytest.approx(2000.0)
        assert abs(hd["RA"] - ra_jnow * 15.0) > 0.05   # genuinely moved off JNOW
    finally:
        await h.disconnect_all()


async def test_solve_saved_lights_stamps_wcs(monkeypatch, tmp_path):
    """When solve_saved_lights is ON, a saved light is solved in place and gets a
    celestial WCS a stacker can read without re-solving (supervisor ruling 4)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    temp_store.cfg().solve_saved_lights = True
    temp_store.bump_and_save()
    # Force the sim solver (deterministic; independent of whether ASTAP is on box).
    from astrodeck.solve import SimSolver
    monkeypatch.setattr("astrodeck.providers.pick_solver",
                        lambda hub: SimSolver(getattr(hub, "sim_rig", None), mode=None))

    h = Hub()
    await h.connect_sim()
    try:
        await h.capture(0.5, 100, 30, 1, save=True, target="M42")
        saved = Path(h.last_frame.saved_path)
        from astropy.wcs import WCS
        with fits.open(saved) as hdul:
            hd = hdul[0].header
            assert hd["CTYPE1"] == "RA---TAN"
            w = WCS(hd)
        assert w.has_celestial
    finally:
        await h.disconnect_all()
