"""NINA bridge backend, exercised against the mock NINA server.

The mock is driven by the same SimRig as the device tests, so autofocus,
plate-solving and centering genuinely converge through the NINA code paths.
"""
import httpx
import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.nina import build_nina_rig, _sep_deg
from astrodeck.focus import run_autofocus
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


@pytest.fixture
async def nina():
    client, state = _mock_client()
    rig = await build_nina_rig("nina.test", 1888, http=client)
    yield rig, state
    await client.aclose()


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


async def test_hub_connect_capture_and_solve(monkeypatch):
    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
    h = hub_module.Hub()
    try:
        summary = await h.connect_nina("nina.test", 1888)
        assert summary["mode"] == "nina"
        assert "camera" in summary["devices"]

        info = await h.capture(1.0, 120, 30, save=True, target="M31")
        assert info["id"] in h.previews
        _png, mime = h.previews[info["id"]]
        assert mime == "image/jpeg"
        assert "hfr" in info
        assert info.get("saved_path")  # NINA reports where it saved

        res = await h.solve_and_sync(1.0)
        assert res["solver"] == "NINA"
        assert abs(res["dec_deg"] - state.rig.dec_deg) < 0.001
    finally:
        await h.disconnect_all()
        await client.aclose()


async def test_hub_goto_and_center_through_nina(monkeypatch):
    client, state = _mock_client()
    real_build = hub_module.build_nina_rig

    async def patched(host, port=1888, http=None):
        return await real_build(host, port, http=client)

    monkeypatch.setattr(hub_module, "build_nina_rig", patched)
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
