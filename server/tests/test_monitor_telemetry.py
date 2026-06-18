"""Monitor + live-preview backend telemetry (Batch 2, lane 2C).

Covers the deterministic ETA / paused-aware elapsed (engine.py), the preview
event shape incl. the star_list overlay payload (hub._publish_preview), and the
server-computed cooler + meridian blocks (hub.poll_status)."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import COOLER_AT_TARGET_C, ETA_MIN_FRAMES


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # Disarm the W1.10 sun-exclusion cone for these date-independent ETA/telemetry
    # mechanics tests (fixed M42 target). The cone is covered by test_sun_guard.py;
    # without this the engine would (correctly) sun-abort whenever the real Sun is
    # within 30 deg of M42. monkeypatch restores the field afterward.
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="eta",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="Ha", exposure_s=0.05, gain=100, count=4)],
        )],
        guide=False, dither_every=0, autofocus_every=0,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# ------------------------------------------------------------------- ETA math

async def test_elapsed_excludes_paused_time(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(_plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
        autofocus_first=False,
        steps=[ExposureStep(filter="Ha", exposure_s=0.4, gain=100, count=3)])]))
    await asyncio.sleep(0.2)
    engine.pause()
    before = engine._elapsed_s()
    await asyncio.sleep(0.6)        # paused — elapsed must not advance
    after = engine._elapsed_s()
    assert after - before < 0.15, (before, after)
    engine.resume()
    await asyncio.sleep(0.2)
    assert engine._elapsed_s() > after
    await engine.abort()


async def test_remaining_capture_excludes_in_flight_frame(sim_hub):
    """The off-by-one guard (spec §5.3): the in-flight frame is the `in_flight`
    term, so it must never also be inside remaining_capture_s."""
    engine = SequenceEngine(sim_hub)
    plan = _plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
        autofocus_first=False,
        steps=[ExposureStep(filter="Ha", exposure_s=1.0, gain=100, count=4)])])
    engine.start(plan)
    # let the first frame begin so _frame_started_at is set mid-exposure
    assert await wait_for(lambda: engine._frame_started_at > 0)
    remaining = engine._remaining_capture_s()
    # 4 frames @1s, one in flight → at most 3s of *other* frames owed
    assert remaining <= 3.0 + 1e-6, remaining
    await engine.abort()


async def test_eta_low_confidence_until_min_frames(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    # immediately after start, no real frames measured → not confident
    eta = engine.compute_eta()
    assert eta["eta_s"] >= 0
    assert eta["eta_confident"] is False
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    # by completion the overhead EMA has >= ETA_MIN_FRAMES samples
    assert engine._overhead_samples >= ETA_MIN_FRAMES


async def test_progress_dict_carries_eta_fields(sim_hub):
    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    assert await wait_for(lambda: "eta_s" in engine.state.get("progress", {}))
    prog = engine.state["progress"]
    for k in ("eta_s", "eta_confident", "server_now_ms", "current_exposure_s",
              "remaining_capture_s", "events_cost_s", "elapsed_s"):
        assert k in prog, k
    await engine.abort()


def test_cooler_at_target_constant_is_shared():
    # one definition (engine), imported by the hub — must agree (master plan §A.7)
    assert COOLER_AT_TARGET_C == Hub._cooler_at_target_c()


# ----------------------------------------------------------- preview event shape

async def test_preview_event_shape_linear_path(sim_hub):
    info = await sim_hub.capture(0.05, 120, 30)
    # the corrected contract: data dims renamed, display dims present, source set
    for k in ("id", "data_width", "data_height", "display_width", "display_height",
              "mime", "source", "is_stretched", "data_is_linear", "has_lossless",
              "full_well", "histogram", "histogram_domain", "auto_levels", "ts"):
        assert k in info, k
    assert "width" not in info and "height" not in info   # renamed, not present
    assert info["source"] == "sim"
    assert info["is_stretched"] is False
    assert info["data_is_linear"] is True
    assert info["mime"] == "image/jpeg"
    assert info["histogram_domain"] == "display"
    assert "histogram_linear" in info
    al = info["auto_levels"]
    assert 0.0 <= al["black"] < al["white"] <= 1.0


async def test_sim_frame_carries_full_well_and_clip_enabled(sim_hub):
    """The clip/saturation pipeline must be live on the linear (sim/Alpaca)
    backends (P1-1): the CameraFrame carries a real full_well + data_is_linear,
    and the published preview event exposes a positive full_well with
    data_is_linear True — exactly the two fields the UI gates `clipAvailable` on.
    A None/0 full_well or data_is_linear False would silently disable the clip
    overlay, star saturation flag, and CLIPPED tag on every sim/Alpaca frame."""
    cam = sim_hub.devices["camera"]
    frame = await cam.expose(0.05, 120, 30)
    # the frame itself carries the saturation ADU and the linear flag
    assert frame.full_well == 65535
    assert frame.data_is_linear is True
    # and the published event rides them across to the UI lanes (cross-lane
    # contract): full_well positive + data_is_linear True ⇒ clip enabled.
    info = await sim_hub.capture(0.05, 120, 30)
    assert info["full_well"] == 65535
    assert info["data_is_linear"] is True
    # the clip "available" condition the UI uses end-to-end
    assert info["data_is_linear"] and info["full_well"] is not None and info["full_well"] > 0


async def test_preview_star_list_marks(sim_hub):
    info = await sim_hub.capture(0.5, 200, 30)   # longer exposure → stars
    assert "star_list" in info
    marks = info["star_list"]
    assert isinstance(marks, list)
    if marks:
        m = marks[0]
        assert set(m) >= {"x", "y", "hfr"}
        assert m["x"] <= info["data_width"]
        assert m["y"] <= info["data_height"]


async def test_local_save_reports_saved_local_in_first_event(sim_hub):
    """P2-2: for a local (sim/Alpaca) save the FITS is written BEFORE the preview
    event, so the first `preview` event already carries saved_local=True and a
    real saved_path — no permanently-stale saved_local=false on the WS consumer."""
    info = await sim_hub.capture(0.05, 120, 30, save=True, target="M42")
    assert info["saved_local"] is True
    assert info["saved_path"] and info["saved_path"].endswith(".fits")
    from pathlib import Path
    assert Path(info["saved_path"]).exists()
    # the ring entry's meta (what the /fits route reads) agrees with the event
    entry = sim_hub.previews[info["id"]]
    assert entry.meta["saved_local"] is True
    assert entry.meta["saved_path"] == info["saved_path"]


async def test_preview_ring_caps_linear_retention(sim_hub):
    # capture several frames; only the latest 1–2 keep linear/lossless (Pi memory)
    ids = []
    for _ in range(5):
        info = await sim_hub.capture(0.05, 120, 30)
        ids.append(info["id"])
    held_linear = [k for k, e in sim_hub.previews.items() if e.linear is not None]
    assert len(held_linear) <= hub_module.PREVIEW_LINEAR_KEEP
    # the newest frame always keeps its lossless base
    assert sim_hub.previews[ids[-1]].lossless is not None


# ----------------------------------------------------------- cooler + meridian

async def test_poll_status_cooler_block(sim_hub):
    # turn the sim cooler on so the power model produces a non-trivial readout
    await sim_hub.devices["camera"].set_cooler(True, -10.0)
    st = await sim_hub.poll_status()
    cooler = st["camera"]["cooler"]
    assert cooler["on"] is True
    assert cooler["can_report_power"] is True
    assert cooler["power"] is not None and 0 <= cooler["power"] <= 100
    assert cooler["target_c"] == -10.0
    assert "at_target" in cooler


async def test_poll_status_meridian_block(sim_hub):
    st = await sim_hub.poll_status()
    mer = st["meridian"]
    # sim mount reports a WEST pier → treated as GEM; status is a real verdict,
    # never the dead "flip off" the draft produced.
    assert mer["status"] in ("counting", "due", "flip_disabled", "n_a_fork", "unknown")
    assert mer["pier_side"] in ("east", "west", "unknown")
    assert "hours_to_flip" in mer and "flip_enabled" in mer


async def test_meridian_counting_with_flip_enabled_plan(sim_hub):
    # register an engine with a flip-enabled plan so flip_enabled flows through
    engine = SequenceEngine(sim_hub)
    engine.plan = _plan(meridian_flip=True)
    st = await sim_hub.poll_status()
    mer = st["meridian"]
    # sim pier=WEST is a GEM and the plan enables flips → not n_a_fork/disabled
    assert mer["status"] in ("counting", "due")
    assert mer["flip_enabled"] is True
    assert mer["hours_to_flip"] is not None


async def test_monitor_snapshot_aggregator(sim_hub):
    await sim_hub.capture(0.05, 120, 30)
    snap = await sim_hub.monitor_snapshot()
    assert set(snap) >= {"sequence", "status", "preview_id", "guide_recent"}
    assert snap["preview_id"] == sim_hub.preview_seq
    assert "camera" in snap["status"]


# ----------------------------------------------------------- preview REST routes

def test_preview_routes_resolve(tmp_path, monkeypatch):
    """The `:int` convertor must keep `/api/preview/{id}` (display JPEG) and
    `/api/preview/{id}.png` (real PNG) distinct — the .png suffix must not 422
    against the integer route."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    from astrodeck.api.app import create_app
    client = TestClient(create_app())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hub_module.hub.connect_sim())
        info = loop.run_until_complete(hub_module.hub.capture(0.05, 120, 30))
        pid = info["id"]
        r = client.get(f"/api/preview/{pid}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        rp = client.get(f"/api/preview/{pid}.png")
        assert rp.status_code == 200 and rp.headers["content-type"] == "image/png"
        assert client.get(f"/api/preview/{pid}/thumb.jpg").status_code == 200
        assert client.get(f"/api/preview/{pid}/lossless.png").status_code == 200
        assert client.get(f"/api/preview/{pid}/png").status_code == 200
        # Pass-2 routes are declared but stubbed 501
        assert client.get(f"/api/preview/{pid}/crop").status_code == 501
        assert client.get(f"/api/preview/{pid}/render.png").status_code == 501
        # an unknown id 404s on both shapes
        assert client.get("/api/preview/99999").status_code == 404
        assert client.get("/api/preview/99999.png").status_code == 404
    finally:
        loop.run_until_complete(hub_module.hub.disconnect_all())
        loop.close()


def test_preview_fits_refuses_path_outside_capture_dir(tmp_path, monkeypatch):
    """The /fits route serves only files under CAPTURE_DIR (path-traversal
    guard, live-preview spec §4.4 security)."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    from astrodeck.api.app import create_app
    client = TestClient(create_app())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hub_module.hub.connect_sim())
        info = loop.run_until_complete(hub_module.hub.capture(0.05, 120, 30))
        pid = info["id"]
        # no saved FITS for a non-saving capture → 404, not a leak
        assert client.get(f"/api/preview/{pid}/fits").status_code == 404
        # an injected path outside CAPTURE_DIR is refused
        hub_module.hub.previews[pid].meta["saved_path"] = "C:/Windows/system.ini"
        assert client.get(f"/api/preview/{pid}/fits").status_code == 404
        # a real file under CAPTURE_DIR is served
        real = tmp_path / "M42" / "Light_M42.fits"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"SIMPLE  =  T")
        hub_module.hub.previews[pid].meta["saved_path"] = str(real)
        assert client.get(f"/api/preview/{pid}/fits").status_code == 200
    finally:
        loop.run_until_complete(hub_module.hub.disconnect_all())
        loop.close()


def test_monitor_snapshot_route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    from astrodeck.api.app import create_app
    client = TestClient(create_app())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hub_module.hub.connect_sim())
        r = client.get("/api/monitor/snapshot")
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"sequence", "status", "preview_id", "guide_recent"}
        assert "running" in body["sequence"]   # live engine state, not just last
    finally:
        loop.run_until_complete(hub_module.hub.disconnect_all())
        loop.close()
