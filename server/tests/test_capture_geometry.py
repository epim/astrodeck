"""Mixed capture settings are visible and advisory, including beyond page one."""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from astropy.io import fits

from astrodeck import capture_geometry as geometry, gallery
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from test_gallery import env  # noqa: F401 -- isolated API fixture
from test_flows_routes import GRAPH
from test_sequence import sim_hub, small_plan, wait_for  # noqa: F401


def row(**over):
    return dict(target="M31", filter="L", frame_type="Light", width=64, height=48,
                bin_x=1, bin_y=1, exposure_s=60, bytes=10) | over


def plan(**step):
    return SequencePlan(name="geometry test", targets=[Target(
        name="M31", ra_hours=0.7, dec_deg=41, steps=[ExposureStep(
            **(dict(filter="L", exposure_s=60, count=1) | step))])])


def write_frame(root, name, *, binning=1, exposure=60, target="M31"):
    hdu = fits.PrimaryHDU(np.zeros((48 // binning, 64 // binning), np.uint16))
    for key, value in dict(OBJECT=target, FILTER="L", IMAGETYP="Light", XBINNING=binning,
                           YBINNING=binning, EXPTIME=exposure).items():
        hdu.header[key] = value
    hdu.writeto(root / name)


def test_full_filtered_counts_not_just_first_page(env):
    client, root = env
    write_frame(root, "one.fits")
    write_frame(root, "two.fits")
    write_frame(root, "three.fits", binning=2, exposure=120)
    body = client.get("/api/gallery/frames", params={"limit": 1}).json()
    assert len(body["frames"]) == 1
    assert body["total"] == 3
    assert [(g["count"], g["width"], g["height"], g["bin_x"], g["exposure_s"])
            for g in body["geometry_groups"]] == [(2, 64, 48, 1, 60), (1, 32, 24, 2, 120)]
    filtered = client.get("/api/gallery/frames", params={"q": "three"}).json()
    assert len(filtered["geometry_groups"]) == 1
    assert filtered["geometry_groups"][0]["count"] == 1


@pytest.mark.parametrize("exposure", ["NaN", "Infinity", "-Infinity", "broken", -1])
def test_invalid_exposure_is_json_safe_without_losing_dimensions_or_date(env, exposure):
    client, root = env
    write_frame(root, "bad-card.fits", exposure=exposure)
    with fits.open(root / "bad-card.fits", mode="update") as hdus:
        hdus[0].header["DATE-OBS"] = "2026-01-02T22:00:00"
    response = client.get("/api/gallery/frames")
    assert response.status_code == 200
    frame = response.json()["frames"][0]
    assert frame["exposure_s"] is None
    assert (frame["width"], frame["height"], frame["bin_x"]) == (64, 48, 1)
    assert frame["ts"] == 1767391200


def test_unknown_metadata_is_not_invented_as_bin_one():
    groups = geometry.geometry_groups([row(bin_x=None, bin_y=None, exposure_s=None)])
    assert groups[0]["bin_x"] is None
    assert "incomplete metadata" in geometry.plan_warnings(plan(), groups)[0]


def test_advisory_matches_target_filter_and_light_type_only():
    groups = geometry.geometry_groups([row(), row(filter="R", bin_x=2, bin_y=2),
                                      row(frame_type="Flat", exposure_s=1), row(target="M42", exposure_s=30)])
    assert geometry.plan_warnings(plan(), groups) == []
    warning = geometry.plan_warnings(plan(binning=2, exposure_s=120), groups)[0]
    assert "planned settings differ" in warning
    assert "64 x 48 px, bin 1 x 1, 60s" in warning
    assert geometry.plan_warnings(plan(filter=None), groups) == []
    assert geometry.plan_warnings(plan(frame_type="Flat"), groups) == []


def test_mixed_history_warns_even_when_plan_matches_modal_group():
    groups = geometry.geometry_groups([row(), row(), row(width=32, height=24, bin_x=2, bin_y=2, exposure_s=120)])
    warnings = geometry.plan_warnings(plan(), groups)
    assert len(warnings) == 1
    assert "2 capture groups" in warnings[0]
    assert "2 frames at 64 x 48" in warnings[0]


def test_actual_dimensions_checked_without_sensor_division():
    groups = geometry.geometry_groups([row(width=3124, height=2088, bin_x=2, bin_y=2)])
    p = plan(binning=2)
    t, step = p.targets[0], p.targets[0].steps[0]
    assert geometry.frame_warning(groups, t, step, {"data_width": 3124, "data_height": 2088}) is None
    assert "captured 3126 x 2088" in geometry.frame_warning(groups, t, step, {"data_width": 3126, "data_height": 2088})
    assert geometry.frame_warning(groups, t, step, {}) is None
    assert geometry.frame_warning(groups, t, step, {"data_width": 900, "data_height": 600,
                                                   "data_is_linear": False}) is None


def test_zero_second_bias_remains_known():
    groups = geometry.geometry_groups([row(frame_type="Bias", exposure_s=0)])
    assert groups[0]["exposure_s"] == 0
    assert geometry.describe(groups[0]).endswith("0s")


async def test_slow_inventory_is_shared_and_does_not_block_event_loop(tmp_path, monkeypatch):
    release = threading.Event()
    calls = []
    def read(root):
        calls.append(root)
        release.wait(2)
        return [], False, time.monotonic()
    monkeypatch.setattr(gallery, "capture_root", lambda: tmp_path)
    monkeypatch.setattr(geometry, "_read_inventory", read)
    try:
        replies = await asyncio.gather(*(geometry.inventory(timeout=.02) for _ in range(8)))
        assert len(calls) == 1
        assert all("still reading" in note for _, note in replies)
    finally:
        release.set()
    assert await geometry.inventory(timeout=2) == ([], None)
    monkeypatch.setattr(geometry, "_TTL_S", -1)
    assert await geometry.inventory(timeout=2, refresh=False) == ([], None)
    assert len(calls) == 1, "late run checks reuse the completed startup scan"


async def test_inventory_failure_is_an_advisory(tmp_path, monkeypatch):
    def fail(root):
        raise OSError("unavailable")
    monkeypatch.setattr(gallery, "capture_root", lambda: tmp_path)
    monkeypatch.setattr(geometry, "_read_inventory", fail)
    assert "could not read" in (await geometry.inventory())[1]


def test_compile_reports_geometry_as_warning_not_unmapped_failure(env):
    client, root = env
    write_frame(root, "banked.fits")
    result = client.post("/api/flows/compile", json={"graph": GRAPH, "name": "test"})
    assert result.status_code == 200
    body = result.json()
    warning = next(i for i in body["issues"] if "largest saved group" in i["text"].lower())
    assert warning["level"] == "warn"
    assert not body["unmapped"]


async def test_capture_logs_new_dimensions_once_and_returns_frame(monkeypatch):
    p = plan()
    target, step = p.targets[0], p.targets[0].steps[0]
    info = {"data_width": 62, "data_height": 48, "saved_path": "frame.fits"}
    hub = SimpleNamespace(capture=AsyncMock(return_value=info))
    engine = SequenceEngine(hub)
    engine._geometry_groups = geometry.geometry_groups([row()])
    engine._geometry_seen = set()
    lines = []
    monkeypatch.setattr("astrodeck.sequence.engine.bus.log", lambda *args: lines.append(args))
    assert await engine._capture(step, target) == info
    assert await engine._capture(step, target) == info
    assert len(lines) == 1
    assert lines[0][0] == "warning" and "captured 62 x 48" in lines[0][1]


async def test_real_sim_sequence_warns_before_capture_and_still_finishes(sim_hub, monkeypatch):
    groups = geometry.geometry_groups([row(target="M42", filter="Ha", exposure_s=10)])
    monkeypatch.setattr(geometry, "inventory", AsyncMock(return_value=(groups, None)))
    lines = []
    monkeypatch.setattr("astrodeck.sequence.engine.bus.log", lambda *args: lines.append(args))
    original = sim_hub.capture
    async def capture(*args, **kwargs):
        assert any("planned settings differ" in line[1] for line in lines)
        return await original(*args, **kwargs)
    monkeypatch.setattr(sim_hub, "capture", capture)
    engine = SequenceEngine(sim_hub)
    engine.start(small_plan())
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert engine.state["progress"]["frames_done"] == 4


async def test_late_inventory_warns_at_frame_boundary_without_restarting_scan(monkeypatch):
    p = plan(binning=2)
    groups = geometry.geometry_groups([row()])
    reader = AsyncMock(return_value=(groups, None))
    monkeypatch.setattr(geometry, "inventory", reader)
    engine = SequenceEngine(SimpleNamespace(capture=AsyncMock(return_value={"data_width": 32, "data_height": 24})))
    engine.plan = p
    engine._geometry_pending = True
    engine._geometry_groups = []
    engine._geometry_seen = set()
    lines = []
    monkeypatch.setattr("astrodeck.sequence.engine.bus.log", lambda *args: lines.append(args))
    await engine._capture(p.targets[0].steps[0], p.targets[0])
    await engine._capture(p.targets[0].steps[0], p.targets[0])
    reader.assert_awaited_once_with(timeout=0.01, refresh=False)
    assert len(lines) == 1 and "planned settings differ" in lines[0][1]
