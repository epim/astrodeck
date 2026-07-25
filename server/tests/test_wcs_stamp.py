"""Per-frame WCS solve + write-back (spec `2026-07-24-per-frame-wcs-design.md`).

PRO-2 F-B shipped the MECHANISM (solvers -> WcsSolution -> write_wcs) and a
naive INLINE wire. This module pins the four things that wire left open, all of
them safety/correctness contracts rather than happy-path plumbing:

* the solve runs OFF the capture hot path (a slow solver must never delay the
  next sub),
* the backlog is bounded drop-oldest and can never raise into a capture,
* the min-stars gate is pure and defaults to "allow everything",
* nothing at all happens when the feature is off or the frame wasn't saved
  locally, and the worker never outlives its hub.

Reuses the sim rig + a tiny stub solver rather than real ASTAP; the scale-less
WCS guards in astap.py/fitsio.py are deliberately untouched and untested here
(they have their own tests — this feature must not relax them).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore, WcsStampConfig
from astrodeck.hub import Hub
from astrodeck.solve.base import SolveResult, WcsSolution
from astrodeck.solve.gate import wcs_should_solve


# --------------------------------------------------------------------- helpers

def _wcs(ra_deg: float = 83.8, dec_deg: float = -5.4) -> WcsSolution:
    """A VALID (scale-bearing) TAN solution — the guard in fitsio._apply_wcs
    drops a scale-less one, so a test WCS must carry a CD matrix."""
    return WcsSolution(crval1=ra_deg, crval2=dec_deg, crpix1=50.0, crpix2=50.0,
                       cd11=-4.3e-4, cd12=0.0, cd21=0.0, cd22=4.3e-4)


class _SlowSolver:
    """Stand-in solver whose solve blocks on an event the test controls, so the
    ordering assertion ("capture returned BEFORE the solve finished") is
    deterministic rather than a sleep race."""
    name = "Slow"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls: list[Path] = []

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None, downsample=0):
        self.calls.append(Path(fits_path))
        self.started.set()
        await self.release.wait()
        return SolveResult(True, message="stub", wcs=_wcs())


@pytest.fixture
async def wcs_hub(tmp_path, monkeypatch):
    """Sim hub with captures + config isolated to tmp_path and the WCS feature
    ON (individual tests turn it back off where that's the point)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(config_mod, "config_store", store)
    store.cfg().solve_saved_lights = True
    store.bump_and_save()
    h = Hub()
    await h.connect_sim()
    yield h, store
    await h.disconnect_all()


async def _drain(h: Hub, timeout: float = 30.0) -> None:
    """Wait for the background worker to finish every queued job."""
    assert h._wcs_queue is not None, "nothing was ever enqueued"
    await asyncio.wait_for(h._wcs_queue.join(), timeout=timeout)


def _has_wcs(path: Path) -> bool:
    with fits.open(path) as hdul:
        return "CTYPE1" in hdul[0].header


# ------------------------------------------------------------- the pure gate

@pytest.mark.parametrize("stars,min_stars,expected", [
    (200, 0, True),      # gate disabled (the default) — always solve
    (3, 20, False),      # junk frame: below the floor, don't burn ASTAP on it
    (50, 20, True),      # comfortably above the floor
    (20, 20, True),      # exactly at the floor is ACCEPTED (>=, not >)
    (None, 20, True),    # unknown count (remote-rendered frame) must not be punished
])
def test_wcs_should_solve(stars, min_stars, expected):
    assert wcs_should_solve(stars, WcsStampConfig(min_stars=min_stars)) is expected


# -------------------------------------------------- the hot-path guarantee (§2.1)

async def test_wcs_solve_runs_off_the_capture_hotpath(wcs_hub, monkeypatch):
    """capture() must RETURN while the solve is still running. Pinned by holding
    the stub solver open across the return: the saved light has no WCS at that
    moment, and only gains one once the worker is allowed to finish."""
    h, _store = wcs_hub
    solver = _SlowSolver()
    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda hub: solver)

    await h.capture(0.5, 100, 30, 1, save=True, target="M42")
    saved = Path(h.last_frame.saved_path)

    # The worker has picked the job up and is parked inside solve()...
    await asyncio.wait_for(solver.started.wait(), timeout=5)
    # ...yet capture() already returned, and the frame is on disk un-stamped.
    assert not _has_wcs(saved), "capture waited for the solve (hot path blocked)"

    solver.release.set()
    await _drain(h)
    assert _has_wcs(saved)


# ------------------------------------------------- bounded backlog / drop-oldest (D2)

async def test_wcs_queue_drops_oldest_and_never_raises(wcs_hub, monkeypatch):
    """A solver slower than the capture cadence must not grow the backlog, wedge
    capture, or propagate anything: the queue stays at queue_max and the NEWEST
    frames are the ones kept."""
    h, store = wcs_hub
    store.cfg().wcs_stamp = WcsStampConfig(queue_max=2)
    store.bump_and_save()
    solver = _SlowSolver()
    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda hub: solver)

    paths = []
    for _ in range(6):
        await h.capture(0.5, 100, 30, 1, save=True, target="M42")
        paths.append(Path(h.last_frame.saved_path))
        assert h._wcs_queue is not None
        # bound holds at every step (the first job is in flight, held by the
        # stub solver, so the queue itself never exceeds queue_max)
        assert h._wcs_queue.qsize() <= 2

    # The retained backlog is the TAIL of the run — dropping is oldest-first.
    pending = list(h._wcs_queue._queue)          # noqa: SLF001 - white-box on purpose
    assert [j.path for j in pending] == paths[-2:]

    solver.release.set()
    await _drain(h)
    # Solved: the in-flight first job plus the two the queue retained. The
    # middle frames simply shipped without WCS; nothing raised, capture never
    # stalled, and the file itself is untouched and valid.
    assert solver.calls == [paths[0], paths[-2], paths[-1]]
    assert _has_wcs(paths[0]) and _has_wcs(paths[-1])
    assert not _has_wcs(paths[1]) and not _has_wcs(paths[2])


# ------------------------------------ no work at all when off / not locally saved

@pytest.mark.parametrize("reason", ["feature-off", "remote-saved"])
async def test_no_wcs_work_when_off_or_not_locally_saved(wcs_hub, monkeypatch,
                                                         reason):
    """OFF (the default) must be byte-identical to before this feature existed:
    no queue, no worker task, no solver call, no WCS cards. Same for a frame the
    backend saved on ITS box (decision D5) — the file isn't here to reopen."""
    h, store = wcs_hub
    if reason == "feature-off":
        store.cfg().solve_saved_lights = False
        store.bump_and_save()
    else:
        # Make the sim camera answer like a remote (NINA) backend: pre-rendered
        # bytes + a saved_path that lives on the imaging host.
        from astrodeck.imaging import to_jpeg
        cam = h.devices["camera"]
        real_expose = cam.expose

        async def _remote_expose(*a, **kw):
            frame = await real_expose(*a, **kw)
            frame.rendered_bytes = to_jpeg(frame.data)[0]
            frame.rendered_mime = "image/jpeg"
            frame.saved_path = r"\\imaging-host\captures\M42_0001.fits"
            return frame

        monkeypatch.setattr(cam, "expose", _remote_expose)

    called: list[Path] = []

    class _Tripwire:
        name = "Tripwire"

        async def solve(self, fits_path, **kw):
            called.append(Path(fits_path))
            return SolveResult(True, wcs=_wcs())

    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda hub: _Tripwire())

    await h.capture(0.5, 100, 30, 1, save=True, target="M42")
    await asyncio.sleep(0)          # give any (wrongly) spawned task a chance to run

    assert called == []
    assert h._wcs_queue is None and h._wcs_task is None
    if reason == "feature-off":
        assert not _has_wcs(Path(h.last_frame.saved_path))


async def test_min_stars_gate_is_wired_to_the_measured_count(wcs_hub, monkeypatch):
    """The gate must read the count the PREVIEW measured, not CameraFrame.stars
    (which only a backend that measures its own frames ever sets) — otherwise
    the whole knob is a silent no-op on exactly the local frames it filters."""
    h, store = wcs_hub
    store.cfg().wcs_stamp = WcsStampConfig(min_stars=100_000)   # nothing passes
    store.bump_and_save()
    called: list[Path] = []

    class _Tripwire:
        name = "Tripwire"

        async def solve(self, fits_path, **kw):
            called.append(Path(fits_path))
            return SolveResult(True, wcs=_wcs())

    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda hub: _Tripwire())
    info = await h.capture(0.5, 100, 30, 1, save=True, target="M42")
    await _drain(h)
    # the sim frame HAS stars — the gate rejected it on the count, not on None
    assert info.get("stars"), "sim frame should carry a measured star count"
    assert called == []
    assert not _has_wcs(Path(h.last_frame.saved_path))


# ------------------------------------------------------ worker lifecycle (R1)

async def test_wcs_worker_is_cancelled_on_teardown(wcs_hub, monkeypatch):
    """The worker must die with its hub — cancelled in the SAME teardown that
    cancels the capture loop — even while parked mid-solve."""
    h, _store = wcs_hub
    solver = _SlowSolver()
    monkeypatch.setattr("astrodeck.providers.pick_solver", lambda hub: solver)

    await h.capture(0.5, 100, 30, 1, save=True, target="M42")
    await asyncio.wait_for(solver.started.wait(), timeout=5)
    task = h._wcs_task
    assert task is not None and not task.done()

    await h.disconnect_all()        # the teardown under test

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert h._wcs_task is None and h._wcs_queue is None


# ------------------------------------------------------- config route (§3 / RBAC)

def test_wcs_config_route_round_trips_and_gates(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    c = TestClient(app_module.create_app())

    body = {"solve_saved_lights": True,
            "wcs_stamp": {"solver": "astap", "downsample": 2, "min_stars": 15,
                          "queue_max": 8}}
    r = c.post("/api/config/wcs", json=body)
    assert r.status_code == 200, r.text
    assert store.cfg().solve_saved_lights is True
    assert store.cfg().wcs_stamp.downsample == 2
    assert store.cfg().wcs_stamp.min_stars == 15
    echoed = r.json()
    assert echoed["solve_saved_lights"] is True
    assert echoed["wcs_stamp"]["solver"] == "astap"

    # a downsample ASTAP has no -z for is rejected at the store, not stamped in
    r = c.post("/api/config/wcs", json={"solve_saved_lights": True,
                                        "wcs_stamp": {"downsample": 3}})
    assert r.status_code == 422
    assert store.cfg().wcs_stamp.downsample == 2      # good value not clobbered
    # "force sim" is not an offered solver (decision D4)
    r = c.post("/api/config/wcs", json={"solve_saved_lights": True,
                                        "wcs_stamp": {"solver": "sim"}})
    assert r.status_code == 422


def test_wcs_config_route_requires_site_optics_cap(tmp_path, monkeypatch):
    """403 without config.site_optics (same cap as the naming/survey routes), and
    the rejected write persists nothing."""
    from astrodeck.auth import (Principal, reset_active_provider,
                                set_active_provider)

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())

    class _Fake:
        name = "fake"

        async def resolve(self, request):
            return Principal(role="custom", email=None,
                             caps=frozenset({"view.status"}), jti=None)

    reset_active_provider()
    set_active_provider(_Fake())
    try:
        with TestClient(app_module.create_app()) as c:
            before = store.cfg().version
            r = c.post("/api/config/wcs", json={"solve_saved_lights": True})
            assert r.status_code == 403
            assert store.cfg().solve_saved_lights is False
            assert store.cfg().version == before
    finally:
        reset_active_provider()
