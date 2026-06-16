"""Regression: plate-solve must hand ASTAP the VERTICAL (height) FOV as its
``-fov`` hint, not the diagonal (P1-2). The diagonal is ~1.2-1.8x larger and
over-widens the solver's scale search.

Also covers the active-Profile cache that keeps ``effective_optics`` off the
disk on the 2s status poll (P2-2)."""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore, Optics
from astrodeck.hub import Hub
from astrodeck.solve.base import SolveResult


class _CapturingSolver:
    """Stand-in solver that records the fov hint it was handed."""
    name = "Capture"

    def __init__(self):
        self.fov_hint = "unset"

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        self.fov_hint = fov_deg_hint
        # answer with the hints so sync() has coordinates
        return SolveResult(True, ra_hours=ra_hint or 0.0, dec_deg=dec_hint or 0.0,
                           pixel_scale_arcsec=1.55, message="captured")


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # isolate config so the test never touches server/config
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    h = Hub()
    await h.connect_sim()
    yield h, temp_store
    await h.disconnect_all()


async def test_solve_uses_vertical_fov_hint(sim_hub, monkeypatch):
    h, store = sim_hub
    # Asymmetric sensor so the height FOV is unambiguously *different* from the
    # diagonal: 6000 x 4000 px, 3.76 um, 530 mm.
    store.set_optics(Optics(
        focal_length_mm=530.0, pixel_size_um=3.76,
        sensor_width_px=6000, sensor_height_px=4000,
        auto_from_camera=False), expected_version=None)

    opt = h.effective_optics()
    assert opt["have_optics"]
    assert opt["fov_h_deg"] != opt["fov_diag_deg"]   # the bug-distinguishing gap

    solver = _CapturingSolver()
    monkeypatch.setattr(hub_module, "get_solver",
                        lambda sim_rig=None, mode=None: solver)

    await h.solve_and_sync(exposure_s=0.05)

    # The hint handed to the solver must be the VERTICAL field, not the diagonal.
    assert solver.fov_hint == pytest.approx(opt["fov_h_deg"])
    assert solver.fov_hint != pytest.approx(opt["fov_diag_deg"])


async def test_effective_optics_active_profile_is_cached(sim_hub, monkeypatch):
    """P2-2: the active-Profile read is served from an in-memory cache, so the
    2s status poll never hits disk. We prove the disk read happens at most once
    per active-id, and that invalidation forces a re-read."""
    h, store = sim_hub
    from astrodeck.profiles import Profile

    reads = {"n": 0}
    real_active = hub_module.profiles.active

    def counting_active(pid):
        reads["n"] += 1
        return real_active(pid)

    monkeypatch.setattr(hub_module.profiles, "active", counting_active)

    # No active profile → no disk read at all.
    h.effective_optics()
    h.effective_optics()
    assert reads["n"] == 0

    # Activate a profile id; first read loads, subsequent reads are cached.
    store.set_active_profile("some-id")
    h.effective_optics()
    h.effective_optics()
    h.effective_optics()
    assert reads["n"] == 1

    # Explicit invalidation forces exactly one more read.
    h.invalidate_profile_cache()
    h.effective_optics()
    assert reads["n"] == 2
