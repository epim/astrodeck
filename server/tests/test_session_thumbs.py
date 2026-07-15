"""Task 6: per-frame review thumbnails (sessions spec §3) — sim frame."""
import asyncio

import pytest

import astrodeck.hub as hub_module
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
