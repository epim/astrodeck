"""NINA bridge backend, exercised against the mock NINA server.

The mock is driven by the same SimRig as the device tests, so autofocus,
plate-solving and centering genuinely converge through the NINA code paths.
"""
from pathlib import Path

import httpx
import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.nina import build_nina_rig, _sep_deg
from astrodeck.focus import run_autofocus
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.solve.base import SolveResult
from tools.mock_nina import create_mock_nina


def test_sep_deg_pole_safe():
    # From the pole, separation is just the declination difference at any RA —
    # this is what makes the convergence-based slew robust near Dec 90.
    assert abs(_sep_deg(9.88, 90.0, 0.70, 85.14) - 4.86) < 0.05
    assert _sep_deg(5.0, 10.0, 5.0, 10.0) < 1e-6
    assert abs(_sep_deg(0.0, 0.0, 12.0, 0.0) - 180.0) < 1e-3


def _mock_client():
    app, state = create_mock_nina()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://nina.test")
    return client, state


class _AstapLikeSolver:
    """Stand-in for the real ASTAP solver in NINA mode (P0-1). NINA-mode solve
    now routes through the local solver (capture -> save_fits -> solve -> sync)
    instead of NINA's broken /prepared-image/solve. This mock answers like ASTAP
    would on a centered frame: it reads the true sim-mount pointing the mock NINA
    is tracking, so the solve->sync->re-slew loop converges. It also records the
    FITS path + hints it was handed, proving the real save_fits->solver pipeline
    ran (not the old NINA endpoint)."""
    name = "ASTAP"

    def __init__(self, rig):
        self.rig = rig
        self.calls: list[dict] = []

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        self.calls.append({"fits_path": fits_path, "ra_hint": ra_hint,
                           "dec_hint": dec_hint, "fov_deg_hint": fov_deg_hint})
        assert Path(fits_path).exists(), "hub must write a FITS for the solver"
        return SolveResult(True, ra_hours=self.rig.ra_hours, dec_deg=self.rig.dec_deg,
                           rotation_deg=0.0, pixel_scale_arcsec=1.55,
                           message="solved (astap-like)")


@pytest.fixture
async def nina():
    client, state = _mock_client()
    rig = await build_nina_rig("nina.test", 1888, http=client)
    yield rig, state
    await client.aclose()


@pytest.fixture
async def nina_hub(monkeypatch, tmp_path):
    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
    # NINA-mode solve now routes through the local (ASTAP) solver, not NINA's
    # broken /prepared-image/solve (P0-1). Provide an ASTAP-like solver + a temp
    # captures dir so any centering through this hub converges in tests without a
    # real ASTAP install.
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # solve_and_sync now resolves its solver via providers.pick_solver (spec
    # §3.4) instead of the removed hub-level get_solver alias; patch the
    # resolver's own seam so the ASTAP-like stand-in is still what gets used
    # (a real NINA-backed rig has no real ASTAP in CI, and the resolver's
    # motion-keyed guard would otherwise refuse to fake-solve it).
    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "pick_solver",
                        lambda hub: _AstapLikeSolver(state.rig))
    h = hub_module.Hub()
    await h.connect_nina("nina.test", 1888)
    yield h, state
    await h.disconnect_all()
    await client.aclose()


async def test_nina_filter_offsets_loaded(nina_hub):
    h, _ = nina_hub
    fw = h.devices["filterwheel"]
    assert fw.filter_offsets == [0, 12, 10, 15, 120, 110, 115]


async def test_meridian_flip_mechanism(nina_hub):
    h, state = nina_hub
    state.ttf = -0.01                       # past the meridian
    result = await h.meridian_flip(9.9258, 69.0653)
    assert state.flip_count >= 1            # the mount flipped pier
    assert result["centered"]              # re-centered via the real solver


class _RecordingGuider:
    """Connected guider that records the flip_calibration + restart ordering, so
    the meridian-flip test can assert the calibration is flipped on the far side
    of the pier BEFORE guiding resumes (review 7d) without a real PHD2 socket."""
    name = "Recording"
    connected = True

    def __init__(self):
        self.events: list[str] = []

    async def is_active(self) -> bool:
        return True

    async def stop_guiding(self) -> None:
        self.events.append("stop")

    async def flip_calibration(self) -> bool:
        self.events.append("flip")
        return True

    async def start_guiding(self) -> None:
        self.events.append("start")


async def test_meridian_flip_flips_guider_calibration(nina_hub):
    """review 7d: after the re-slew, the hub flips the guider's calibration for
    the far side of the pier BEFORE restarting guiding, so guiding does not run
    backwards (runaway) on a GEM."""
    h, state = nina_hub
    guider = _RecordingGuider()
    h.guider = guider
    state.ttf = -0.01
    await h.meridian_flip(9.9258, 69.0653)
    # stop -> flip_calibration -> start, in that order
    assert guider.events == ["stop", "flip", "start"]


async def test_meridian_flip_survives_guider_without_flip(nina_hub):
    """A guider lacking flip_calibration (or one that raises) must not break the
    flip — the call is guarded best-effort."""
    class _NoFlip:
        name = "NoFlip"
        connected = True

        def __init__(self):
            self.started = False

        async def is_active(self):
            return True

        async def stop_guiding(self):
            pass

        async def start_guiding(self):
            self.started = True

    h, state = nina_hub
    g = _NoFlip()
    h.guider = g
    state.ttf = -0.01
    result = await h.meridian_flip(9.9258, 69.0653)
    assert result["centered"]
    assert g.started            # guiding still restarted despite no flip method


async def test_engine_flip_triggers_on_server_ha_not_device(nina_hub, monkeypatch):
    """The REAL trigger path (not the old faked ``state.ttf = -0.01``): the engine
    computes hours-to-flip server-side from the hour angle and flips when the
    target crosses the meridian — even though the NINA device reports a large
    POSITIVE time-to-flip (its value wraps ~0→12h at the crossing and is never
    negative, which is exactly why the old ``ttf <= 0`` device gate could never
    fire on real gear).
    """
    from astrodeck.catalog.coords import lst_hours

    h, state = nina_hub
    h.devices.pop("focuser", None)          # skip post-flip autofocus for speed
    # a real GEM past the meridian still reports a POSITIVE wrapped value here.
    state.ttf = 11.9
    # the target-near-the-meridian coords below land wherever the sky is now, so
    # disarm the W1.10 sun cone (covered by test_sun_guard) to keep this test
    # date/time independent.
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)

    flips: list[tuple] = []
    real_flip = h.meridian_flip

    async def spy_flip(ra, dec):
        flips.append((ra, dec))
        return await real_flip(ra, dec)

    monkeypatch.setattr(h, "meridian_flip", spy_flip)

    engine = SequenceEngine(h)
    engine.plan = SequencePlan(meridian_flip=True)
    # _setup_target arms the flip when a target is acquired EAST of the meridian;
    # simulate that here (we call _maybe_meridian_flip directly, bypassing setup).
    engine._flip_armed = True
    lon = h.site["longitude"]

    # target still ~3h EAST of the meridian (HA = -3h) → server countdown +3h,
    # far beyond the frame window → NOT due, no flip, despite the +11.9h device.
    east = Target(name="east", ra_hours=(lst_hours(lon) + 3.0) % 24.0,
                  dec_deg=45.0, steps=[])
    await engine._maybe_meridian_flip(east, next_exposure_s=1.0)
    assert flips == []

    # target just PAST the meridian (HA ~ +3 min, server countdown < 0) → due →
    # the engine flips even though the device still says +11.9h.
    west = Target(name="west", ra_hours=(lst_hours(lon) - 0.05) % 24.0,
                  dec_deg=45.0, steps=[])
    await engine._maybe_meridian_flip(west, next_exposure_s=1.0)
    assert flips, ("engine must flip on the server hour-angle countdown even when "
                   "the device reports a positive (wrapped) time-to-flip")


async def test_build_registers_connected_devices(nina):
    rig, _ = nina
    assert set(rig["devices"]) >= {"camera", "telescope", "focuser",
                                   "filterwheel", "switch"}
    assert rig["guider"] is not None


async def test_camera_expose_returns_rendered_frame(nina):
    rig, _ = nina
    frame = await rig["devices"]["camera"].expose(1.0, 120, 30, binning=1)
    assert frame.rendered_bytes and len(frame.rendered_bytes) > 1000
    assert frame.rendered_mime == "image/jpeg"
    assert frame.hfr is not None and frame.hfr > 0
    assert frame.stars is not None and frame.stars > 0
    assert frame.data.ndim == 2  # decoded grayscale for the histogram


async def test_mount_slew_and_sync(nina):
    rig, state = nina
    tel = rig["devices"]["telescope"]
    await tel.slew(10.0, 41.0)
    ra, dec = await tel.get_position()
    assert abs(dec - 41.0) < 0.2
    await tel.sync(10.0, 41.0)
    assert state.rig.pointing_error_deg < 0.01


async def test_mount_move_axis_unsupported(nina):
    rig, _ = nina
    with pytest.raises(Exception):
        await rig["devices"]["telescope"].move_axis("ra", 1.0)


async def test_native_autofocus_curve(nina):
    rig, state = nina
    foc = rig["devices"]["focuser"]
    assert foc.supports_native_autofocus
    res = await foc.native_autofocus()
    assert res["success"], res
    assert abs(res["best_position"] - state.rig.best_focus) <= 1
    assert len(res["points"]) >= 5
    assert res["best_hfr"] is not None


async def test_run_autofocus_delegates_to_native(nina):
    rig, state = nina
    result = await run_autofocus(rig["devices"]["camera"], rig["devices"]["focuser"])
    assert result.success
    assert abs(result.best_position - state.rig.best_focus) <= 1
    assert result.points


async def test_filterwheel_named_filters(nina):
    rig, _ = nina
    fw = rig["devices"]["filterwheel"]
    assert "Ha" in fw.filter_names
    slot = fw.filter_names.index("Ha")
    await fw.set_position(slot)
    assert await fw.get_position() == slot


async def test_switch_ports(nina):
    rig, _ = nina
    ports = await rig["devices"]["switch"].get_ports()
    assert any(p.can_write for p in ports)
    assert any(not p.can_write for p in ports)
    boolean = next(p for p in ports if p.can_write and p.is_boolean)
    await rig["devices"]["switch"].set_port(boolean.id, 1)


async def test_hub_connect_capture_and_solve(monkeypatch, tmp_path):
    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
    # keep solver scratch FITS off the real captures volume
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # NINA-mode solve now routes through the local (ASTAP) solver, not NINA's
    # broken /prepared-image/solve (P0-1). Stand in for ASTAP so the test has a
    # working local solver without a real ASTAP install. solve_and_sync resolves
    # its solver via providers.pick_solver (spec §3.4), not the removed
    # hub-level get_solver alias, so patch that seam instead.
    solver = _AstapLikeSolver(state.rig)
    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)
    h = hub_module.Hub()
    try:
        summary = await h.connect_nina("nina.test", 1888)
        assert summary["mode"] == "nina"
        assert "camera" in summary["devices"]

        info = await h.capture(1.0, 120, 30, save=True, target="M31")
        assert info["id"] in h.previews
        entry = h.previews[info["id"]]   # PreviewEntry ring slot (live-preview spec §6)
        assert entry.mime == "image/jpeg"
        assert info["mime"] == "image/jpeg"
        # NINA is a pre-rendered, decoded-from-render path → not linear, no clip
        assert info["is_stretched"] is True
        assert info["data_is_linear"] is False
        assert "hfr" in info
        assert info.get("saved_path")  # NINA reports where it saved

        res = await h.solve_and_sync(1.0)
        # routed through the REAL local solver, not NINA's /prepared-image/solve
        assert res["solver"] == "ASTAP"
        assert abs(res["dec_deg"] - state.rig.dec_deg) < 0.001
        # the hub captured a frame, wrote a FITS, and passed the mount hint
        assert solver.calls, "solver was never reached"
        assert solver.calls[0]["dec_hint"] == pytest.approx(state.rig.dec_deg)
    finally:
        await h.disconnect_all()
        await client.aclose()


async def test_hub_goto_and_center_through_nina(monkeypatch, tmp_path):
    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    solver = _AstapLikeSolver(state.rig)
    # solve_and_sync now resolves its solver via providers.pick_solver (spec
    # §3.4), not the removed hub-level get_solver alias.
    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)
    # Disarm the W1.10 sun cone: this fixed (10h, +30) target falls within 30 deg
    # of the Sun for a few weeks each year (late Aug). The cone is covered by
    # test_sun_guard.py; here we test NINA goto-center mechanics date-independently.
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = hub_module.Hub()
    try:
        await h.connect_nina("nina.test", 1888)
        result = await h.goto_and_center(10.0, 30.0, solve_exposure_s=0.05)
        assert result["centered"], result
        _ra, dec = await h.require("telescope").get_position()
        assert abs(dec - 30.0) < 0.05
    finally:
        await h.disconnect_all()
        await client.aclose()


async def test_connect_nina_unreachable_reraises_deviceerror(monkeypatch):
    """W1.3.0/T2(12) fail-fast: when build_nina_rig raises (NINA unreachable),
    connect_profile opens NO nina session, so the thin connect_nina builder
    detects total-primary-failure (no key[0]=='nina' in sessions) and re-raises
    the recorded RoleResult.error as a DeviceError -- NOT a silent partial connect
    or an opaque StopIteration. ``failures`` is now a derived alias; the reason is
    read from the not-ok ``results``."""
    from astrodeck.devices.base import DeviceError

    async def boom(host, port=1888, http=None):
        raise DeviceError(f"cannot reach NINA at {host}:{port}")

    monkeypatch.setattr(hub_module, "build_nina_rig", boom)
    h = hub_module.Hub()
    try:
        with pytest.raises(DeviceError) as ei:
            await h.connect_nina("nina.test", 1888)
        assert "cannot reach NINA" in str(ei.value)
        # nothing partially connected.
        assert h.devices == {}
    finally:
        await h.disconnect_all()


async def test_nina_solve_refuses_without_astap(monkeypatch, tmp_path):
    """No ASTAP installed on a real (NINA) rig → the resolver's motion-keyed
    guard REFUSES the simulator solver (review finding 6 / spec §3.4):
    solve_and_sync raises a clear error BEFORE any exposure is wasted, instead
    of silently fake-centering the real mount on the pointing hint.
    goto_and_center then degrades to a raw GoTo (centered=False) rather than
    reporting a bogus solve."""
    from astrodeck.devices.base import DeviceError

    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # Force the ASTAP-not-found path so the resolver's ``solve`` capability
    # refuses (real motion connected, no trustworthy solver). solve_and_sync
    # now resolves via providers.pick_solver (spec §3.4), which reads
    # providers.find_astap — not the removed hub-level get_solver alias — so
    # patch that binding directly rather than relying on this dev box
    # genuinely lacking an ASTAP install.
    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    # Disarm the W1.10 sun cone (see test_hub_goto_and_center_through_nina): the
    # (10h, +30) target is seasonally within the cone; here we test the no-ASTAP
    # graceful-degrade path, not sun avoidance.
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = hub_module.Hub()
    try:
        await h.connect_nina("nina.test", 1888)
        # direct solve must raise, not echo the hint as a centered solution
        with pytest.raises(DeviceError):
            await h.solve_and_sync(0.05)
        # resolver-first ordering (spec §3.4): the failure lands BEFORE any
        # exposure is taken, so no preview was ever published.
        assert h.previews == {}
        # centering degrades gracefully to a raw GoTo instead of a fake solve
        result = await h.goto_and_center(10.0, 30.0, solve_exposure_s=0.05)
        assert result["centered"] is False
        assert result.get("solve_failed") is True
    finally:
        await h.disconnect_all()
        await client.aclose()
