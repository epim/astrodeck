"""Task 6: per-frame review thumbnails (sessions spec §3) — sim frame."""
import asyncio
import threading

import pytest

import astrodeck.hub as hub_module
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence import session as session_mod
from astrodeck.sequence import engine as engine_mod
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan() -> SequencePlan:
    return SequencePlan(name="thumb", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=1)])])


async def test_sim_frame_gets_thumbnail(sim_hub):
    plan = _plan()
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    def thumb_done():
        s = session_store.load(sid)
        return bool(s.frames) and s.frames[0].thumb is not None
    # the render task is fire-and-forget: poll until it lands
    assert await wait_for(thumb_done, timeout=15.0)
    s = session_store.load(sid)
    assert s.frames[0].thumb == f"thumbs/{s.frames[0].id}.jpg"
    p = session_mod._sessions_dir() / sid / s.frames[0].thumb
    assert p.exists() and p.stat().st_size > 0
    assert p.read_bytes()[:2] == b"\xff\xd8"       # JPEG magic


async def test_rejected_frame_also_gets_thumbnail(sim_hub, monkeypatch):
    """The review UI needs rejected frames' thumbs most (spec §3) — force every
    frame to reject via the quality gate and confirm the ledger entry still
    gets a rendered thumb."""
    monkeypatch.setattr(engine_mod.SequenceEngine, "_check_quality",
                        lambda self, info, calibration=False: False)
    plan = _plan()
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") in ("complete", "aborted"))

    def thumb_done():
        s = session_store.load(sid)
        return bool(s.frames) and s.frames[0].thumb is not None
    assert await wait_for(thumb_done, timeout=15.0)
    s = session_store.load(sid)
    assert s.frames[0].auto_accepted is False
    assert s.frames[0].thumb == f"thumbs/{s.frames[0].id}.jpg"
    p = session_mod._sessions_dir() / sid / s.frames[0].thumb
    assert p.exists() and p.stat().st_size > 0


async def test_thumb_render_failure_is_silent_best_effort(sim_hub, monkeypatch):
    """A broken encoder must never block or fail capture: the frame still
    records with thumb left None, and only a warning is logged."""
    def _boom(*a, **kw):
        raise RuntimeError("encoder exploded")
    monkeypatch.setattr(engine_mod, "to_jpeg", _boom)

    plan = _plan()
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    # give the fire-and-forget render task a chance to run and fail
    await asyncio.sleep(0.5)
    s = session_store.load(sid)
    assert len(s.frames) == 1
    assert s.frames[0].thumb is None
    tdir = session_mod._sessions_dir() / sid / "thumbs"
    assert not tdir.exists() or not any(tdir.iterdir())


async def test_thumb_render_backpressure_drops_when_saturated(sim_hub, monkeypatch):
    """Task 6 review (Important #2 — no back-pressure): every recorded frame
    used to unconditionally spawn a render holding a full-res ndarray
    closure, so a fast calibration burst would queue unbounded in-flight
    renders. The fix is DROP-when-saturated: once
    ``engine_mod._MAX_PENDING_THUMBS`` renders are in flight, later frames'
    spawn is skipped outright (thumb stays None) — never queued, never
    awaited from the capture loop.

    Gate the encoder shut for the whole run so every render that DOES get
    spawned stays pending throughout capture: that makes the cap exact (a
    queueing semaphore would drain back toward 0 as frames finish; a drop
    policy holds steady at the cap)."""
    gate = threading.Event()

    def _gated_to_jpeg(data, **kw):
        gate.wait(timeout=30.0)
        return b"\xff\xd8FAKE", 8, 8

    monkeypatch.setattr(engine_mod, "to_jpeg", _gated_to_jpeg)

    cap = engine_mod._MAX_PENDING_THUMBS
    n = cap + 4   # comfortably over the cap ("> 4 rapid records")
    plan = SequencePlan(name="thumb-burst", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.02,
                                                count=n)])])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    try:
        # the burst must complete normally -- the capture loop never blocks
        # on a saturated thumb queue.
        assert await wait_for(lambda: eng.state.get("state") == "complete")
        # pending set never exceeds the cap, and (since every render is
        # gated shut) lands EXACTLY at the cap once the burst is done.
        assert len(eng._thumb_tasks) == cap

        s = session_store.load(sid)
        assert len(s.frames) == n
        assert all(f.thumb is None for f in s.frames[cap:])   # dropped tail
    finally:
        gate.set()   # release the gated encoder threads so they don't leak

    # once the gate opens, the cap's-worth of pending renders land normally
    def cap_thumbs_done():
        s = session_store.load(sid)
        return all(f.thumb is not None for f in s.frames[:cap])
    assert await wait_for(cap_thumbs_done, timeout=15.0)
    assert await wait_for(lambda: len(eng._thumb_tasks) == 0, timeout=15.0)
    # the dropped tail must STAY dropped forever -- no task was ever spawned
    s = session_store.load(sid)
    assert all(f.thumb is None for f in s.frames[cap:])


async def test_abort_drains_pending_thumb_tasks(sim_hub, monkeypatch):
    """Task 6 review (Important #1 — untracked tasks): ``_render_thumb`` used
    to be spawned via bare ``asyncio.create_task`` with no reference kept, so
    ``abort()`` could neither cancel nor await a pending render -> an
    orphaned task ("Task was destroyed but it is pending" at interpreter
    exit). ``abort()`` must now cancel every pending thumb task and await it
    so teardown is clean (task set empties, no warnings) and bounded (a slow
    encoder can't hang shutdown)."""
    gate = threading.Event()

    def _gated_to_jpeg(data, **kw):
        gate.wait(timeout=30.0)
        return b"\xff\xd8FAKE", 8, 8

    monkeypatch.setattr(engine_mod, "to_jpeg", _gated_to_jpeg)

    plan = _plan()   # single short frame
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    try:
        assert await wait_for(lambda: eng.state.get("state") == "complete")
        # the run's own frame render is gated shut -> still pending right now
        assert len(eng._thumb_tasks) == 1
        pending_task = next(iter(eng._thumb_tasks))

        warnings_before = len([e for e in bus.log_history
                               if e["data"].get("level") == "warning"])
        t0 = asyncio.get_event_loop().time()
        await asyncio.wait_for(eng.abort(), timeout=5.0)
        dt = asyncio.get_event_loop().time() - t0
        assert dt < 5.0                        # bounded -- never hangs on the gate

        assert pending_task.done()
        assert len(eng._thumb_tasks) == 0      # drained -- nothing orphaned
        warnings_after = len([e for e in bus.log_history
                              if e["data"].get("level") == "warning"])
        assert warnings_after == warnings_before
    finally:
        gate.set()
